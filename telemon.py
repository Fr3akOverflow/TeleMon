#!/usr/bin/env python3
"""TeleMon - Telegram Systemüberwachung.

Einfache Ressourcen-Überwachung mit Alerts über den Telegram Bot API.
Für einen einzelnen Server: kein Prometheus, kein Grafana, keine Datenbank.
"""

import argparse
import json
import logging
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
import requests
import toml

logger = logging.getLogger("telemon")

BASE_DIR = Path(__file__).resolve().parent

STATE_FILE = BASE_DIR / "data" / "state.json"


def load_config(path=Path("config.toml")):
    cfg_path = Path(path)
    if not cfg_path.exists():
        return {}
    return toml.load(cfg_path)


def ensure_state():
    BASE_DIR.joinpath("data").mkdir(exist_ok=True)


def load_state():
    ensure_state()
    if STATE_FILE.exists():
        raw = STATE_FILE.read_text()
        if raw.strip():
            return json.loads(raw)
    return {}


def save_state(state):
    ensure_state()
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_metrics():
    return {
        "host": platform.node(),
        "boot": time.time() - psutil.boot_time(),
        "cpu": psutil.cpu_percent(interval=1),
        "mem": psutil.virtual_memory(),
        "disk": psutil.disk_usage("/"),
        "load": os.getloadavg(),
        "net": psutil.net_io_counters(),
    }


def format_duration(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return " ".join(parts) or "0m"


def fmt(value, unit="%"):
    return f"{value:.1f}{unit}"


def get_chat_ids(cfg):
    tg = cfg.get("telegram", {})
    ids = tg.get("chat_ids") or tg.get("chat_id") or []
    if not isinstance(ids, list):
        ids = [ids]
    out = []
    for i in ids:
        try:
            out.append(int(i))
        except (TypeError, ValueError):
            logger.warning("Überspringe ungültige Chat-ID: %r", i)
    return out


def send_telegram(cfg, text, chat_id=None):
    token = cfg.get("telegram", {}).get("bot_token")
    if not token:
        logger.error("config.toml: telegram.bot_token fehlt")
        return
    targets = [chat_id] if chat_id is not None else get_chat_ids(cfg)
    for cid in targets:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={"chat_id": cid, "text": text}, timeout=30)
        if resp.status_code != 200:
            logger.error("Telegram Fehler %s: %s", resp.status_code, resp.text[:200])


def save_config(cfg_path, cfg):
    cfg_path = Path(cfg_path)
    cfg_path.write_text(
        "# TeleMon Konfiguration - automatisch verwaltet per Telegram\n\n"
        + toml.dumps(cfg)
    )


HELP_TEXT = (
    "🤖 TeleMon Befehle:\n\n"
    "/status     - aktueller Systemstatus\n"
    "/help       - diese Hilfe\n"
    "/settings   - Einstellungen anzeigen\n"
    "/set <name> <wert> - Einstellung ändern\n"
    "             (cpu, mem, disk, load, interval)\n"
    "/clients    - Liste der Empfänger\n"
    "/addclient <id>  - neuen Empfänger hinzufügen\n"
    "/delclient <id>  - Empfänger entfernen\n"
    "/speedtest [30|60|120] - Bandbreitentest-Daten des Zeitfensters\n\n"
    "Alerts: CPU/RAM/Disk/Load werden überwacht.\n"
    "Nachrichten kommen nur bei Über-/Unterschreiten."
)


def format_settings(cfg):
    t = cfg.get("thresholds", {})
    iv = cfg.get("interval", {}).get("seconds", 300)
    return (
        "⚙️ Einstellungen:\n\n"
        f"CPU-Schwelle    : {t.get('cpu_percent', 90)}%\n"
        f"RAM-Schwelle    : {t.get('memory_percent', 90)}%\n"
        f"Disk-Schwelle   : {t.get('disk_percent', 85)}%\n"
        f"Load1-Schwelle  : {t.get('load1', 4.0)}\n"
        f"Intervall       : {iv}s\n\n"
        "Ändern z. B.: /set cpu 80   /set interval 600\n"
        "Clients: /clients · /addclient <id> · /delclient <id>"
    )


def format_clients(cfg):
    ids = get_chat_ids(cfg)
    lines = [f"👥 Empfänger ({len(ids)}):"]
    lines += [f"• {i}" for i in ids]
    return "\n".join(lines)


SETTINGS_ALIASES = {
    "cpu": "cpu_percent",
    "mem": "memory_percent",
    "ram": "memory_percent",
    "disk": "disk_percent",
    "load": "load1",
    "interval": "seconds",
}


def restart_timer(cfg):
    """Schreibe telemon.timer mit aktuellem Intervall neu und starte ihn."""
    try:
        iv = int(cfg.get("interval", {}).get("seconds", 300))
        Path("/etc/systemd/system/telemon.timer").write_text(
            f"""[Unit]
Description=TeleMon Systemüberwachung Timer

[Timer]
OnUnitActiveSec={iv}
OnBootSec=60

[Install]
WantedBy=timers.target
"""
        )
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=15)
        subprocess.run(["systemctl", "restart", "telemon.timer"], check=True, timeout=15)
        return True
    except Exception as e:
        logger.error("Timer-Update fehlgeschlagen: %s", e)
        return False


def apply_setting(cfg, cfg_path, key, value):
    try:
        value = float(value)
    except ValueError:
        return f"'{value}' ist keine gültige Zahl."
    if key not in SETTINGS_ALIASES:
        return "Unbekannte Einstellung. Erlaubt: cpu, mem, disk, load, interval."
    field = SETTINGS_ALIASES[key]
    if field == "seconds":
        if value < 10:
            return "Intervall muss mindestens 10s sein."
        cfg.setdefault("interval", {})["seconds"] = int(value)
        save_config(cfg_path, cfg)
        ok = restart_timer(cfg)
        note = "" if ok else "\n⚠️ Timer konnte nicht neu gestartet werden (kein systemd?)."
        return f"✅ interval auf {value:g}s gesetzt.{note}"
    else:
        if not 0 < value < 100:
            return "Schwellwert muss zwischen 0 und 100 liegen."
        cfg.setdefault("thresholds", {})[field] = value
    save_config(cfg_path, cfg)
    return f"✅ {key} auf {value:g} gesetzt."


def handle_addclient(cfg, cfg_path, raw_id, sender):
    try:
        new_id = int(raw_id)
    except ValueError:
        return f"'{raw_id}' ist keine gültige Chat-ID."
    if new_id in get_chat_ids(cfg):
        return f"{new_id} ist bereits Empfänger."
    cfg.setdefault("telegram", {})["chat_ids"] = get_chat_ids(cfg) + [new_id]
    save_config(cfg_path, cfg)
    send_telegram(cfg, "👋 Du bist jetzt TeleMon-Empfänger. /help für Befehle.", chat_id=new_id)
    return f"✅ {new_id} hinzugefügt und benachrichtigt."


def handle_delclient(cfg, cfg_path, raw_id, sender):
    try:
        del_id = int(raw_id)
    except ValueError:
        return f"'{raw_id}' ist keine gültige Chat-ID."
    ids = get_chat_ids(cfg)
    if del_id not in ids:
        return f"{del_id} ist kein Empfänger."
    if len(ids) <= 1:
        return "Der letzte Empfänger kann nicht entfernt werden."
    cfg.setdefault("telegram", {})["chat_ids"] = [i for i in ids if i != del_id]
    save_config(cfg_path, cfg)
    return f"✅ {del_id} entfernt."


def poll_commands(cfg, cfg_path, timeout=0):
    """Beantworte eingehende Bot-Kommandos (nur von konfigurierten Chats)."""
    token = cfg.get("telegram", {}).get("bot_token")
    chat_ids = set(get_chat_ids(cfg))
    if not token or not chat_ids:
        return
    state = load_state()
    offset = state.get("last_update_id", -1) + 1
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
            timeout=timeout + 10,
        )
        replied = False
        max_id = state.get("last_update_id", -1)
        for update in resp.json().get("result", []):
            max_id = max(max_id, update["update_id"])
            msg = update.get("message") or {}
            sender = msg.get("chat", {}).get("id")
            if sender not in chat_ids:
                continue
            text = (msg.get("text") or "").strip().split("@")[0]
            parts = text.split()
            reply = None
            cmd = parts[0] if parts else ""
            if cmd in ("/start", "/help"):
                reply = HELP_TEXT
            elif cmd == "/status":
                reply = build_report(cfg)
            elif cmd == "/settings":
                reply = format_settings(cfg)
            elif cmd == "/clients":
                reply = format_clients(cfg)
            elif cmd == "/speedtest":
                if len(parts) == 1:
                    reply = band_report()
                elif len(parts) == 2 and parts[1].isdigit() and int(parts[1]) in (30, 60, 120):
                    reply = band_report(int(parts[1]))
                else:
                    reply = "Nur 30, 60 oder 120 Minuten erlaubt (z. B. /speedtest 60)."
            elif cmd == "/set" and len(parts) == 3:
                reply = apply_setting(cfg, cfg_path, parts[1].lower(), parts[2])
            elif cmd == "/addclient" and len(parts) == 2:
                reply = handle_addclient(cfg, cfg_path, parts[1], sender)
            elif cmd == "/delclient" and len(parts) == 2:
                reply = handle_delclient(cfg, cfg_path, parts[1], sender)
            if reply is not None:
                send_telegram(cfg, reply, chat_id=sender)
                replied = True
        if max_id >= 0:
            state["last_update_id"] = max_id
            save_state(state)
        return replied
    except Exception as e:
        logger.error("getUpdates Fehler: %s", e)
        return False


def listen(cfg, cfg_path):
    """Warte dauerhaft auf Kommandos und antworte sofort (Long-Polling)."""
    logger.info("TeleMon Listener gestartet (Long-Polling)")
    while True:
        try:
            poll_commands(cfg, cfg_path, timeout=50)
        except KeyboardInterrupt:
            return 0
        except Exception as e:
            logger.error("Listener Fehler: %s", e)
            time.sleep(2)


def detect_companions():
    """Erkennt installierte Begleitprojekte (netmon, Bandbreitentest) an ihrer systemd-Unit."""
    units = Path("/etc/systemd/system")
    found = []
    if (units / "netmon.service").exists():
        found.append("netmon")
    if (units / "bandbreite-app.service").exists():
        found.append("Bandbreitentest")
    return found


BAND_DATA = Path("/home/bandbreitentest/Bandbreitentest/data.json")

BAND_FIELDS = (
    ("Ping", "ping", "ms"),
    ("Download", "download", "Mbit/s"),
    ("Upload", "upload", "Mbit/s"),
)


def band_report(minutes=30):
    """Aggregiere Bandbreitentest-Messungen der letzten `minutes` Minuten."""
    try:
        data = json.loads(BAND_DATA.read_text())
    except (OSError, ValueError):
        return "⚠️ Bandbreitentest ist nicht installiert oder data.json fehlt/defekt."
    cutoff = time.time() - minutes * 60
    rows = [e for e in data if isinstance(e, dict) and e.get("timestamp", 0) >= cutoff]
    if not rows:
        return f"Keine Messungen in den letzten {minutes} Min."
    lines = [f"📊 Bandbreitentest · letzte {minutes} Min", f"Messungen: {len(rows)}"]
    for label, key, unit in BAND_FIELDS:
        vals = [e[key] for e in rows if e.get(key) is not None]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        lines.append(
            f"{label:<9}: {avg:.1f} {unit} (min {min(vals):.1f} / max {max(vals):.1f})"
        )
    return "\n".join(lines)


def build_report(cfg):
    m = get_metrics()
    t = cfg.get("thresholds", {})
    cpu_max = t.get("cpu_percent", 90)
    mem_max = t.get("memory_percent", 90)
    disk_max = t.get("disk_percent", 85)

    mem = m["mem"].percent
    disk = m["disk"].percent
    cpu = m["cpu"]
    load = m["load"][0]

    lines = [
        f"🖥 {m['host']}",
        f"⏱ Uptime: {format_duration(m['boot'])}",
        f"CPU   : {fmt(cpu)} (Load {load:.2f})",
        f"RAM   : {fmt(mem)} ({m['mem'].used // 2**20} MB / {m['mem'].total // 2**20} MB)",
        f"Disk  : {fmt(disk)} ({m['disk'].free // 2**20} MB frei)",
        f"⬆️  up   : {m['net'].bytes_sent // 2**20} MB",
        f"⬇️  down : {m['net'].bytes_recv // 2**20} MB",
        "",
    ]
    extras = detect_companions()
    if extras:
        lines.append("🧩 Erweiterungen: " + ", ".join(extras))
    alerts = []
    if cpu >= cpu_max:
        alerts.append(f"⚠️ CPU {fmt(cpu)} ≥ {cpu_max}%")
    if mem >= mem_max:
        alerts.append(f"⚠️ RAM {fmt(mem)} ≥ {mem_max}%")
    if disk >= disk_max:
        alerts.append(f"⚠️ Disk {fmt(disk)} ≥ {disk_max}%")

    if alerts:
        lines.append("\n".join(alerts))
    else:
        lines.append("✅ Alle Werte im Rahmen")
    return "\n".join(lines)


def send_alerts_from_state(cfg):
    """Sende Report, wenn ein Threshold über-/unterschritten oder das System neu gebootet wurde."""
    state = load_state()
    t = cfg.get("thresholds", {})
    cpu_max = t.get("cpu_percent", 90)
    mem_max = t.get("memory_percent", 90)
    disk_max = t.get("disk_percent", 85)

    m = get_metrics()
    current = {
        "cpu": m["cpu"] >= cpu_max,
        "mem": m["mem"].percent >= mem_max,
        "disk": m["disk"].percent >= disk_max,
        "load": m["load"][0] >= t.get("load1", 4.0),
    }

    changed = False
    for key in ("cpu", "mem", "disk", "load"):
        if state.get(key, False) != current[key]:
            state[key] = current[key]
            changed = True

    if state.get("last_boot") not in (None, m["boot"]):
        changed = True
        current["__booted"] = True
    state["last_boot"] = m["boot"]

    if changed:
        save_state(state)
        send_telegram(cfg, build_report(cfg))
    return state


def test_connection(cfg):
    token = cfg.get("telegram", {}).get("bot_token")
    if not token:
        print("kein bot_token in config.toml")
        return 1
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        resp = requests.get(url, timeout=30)
        print("OK:", resp.json().get("result", {}).get("username"))
        return 0
    except Exception as e:
        print("Fehler:", e)
        return 1


def reset_state():
    ensure_state()
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    print("Zurückgesetzt: kein Alarm-Zustand, nächster Check meldet wieder normal.")


def report(cfg):
    print(build_report(cfg))


def main():
    ap = argparse.ArgumentParser(description="TeleMon - Telegram Systemüberwachung")
    sub = ap.add_subparsers(dest="cmd")

    for name in ("check", "report", "test", "reset", "listen"):
        p = sub.add_parser(name)
        p.add_argument("-c", "--config", default=str(BASE_DIR / "config.toml"))
    args = ap.parse_args()

    cfg = load_config(getattr(args, "config", BASE_DIR / "config.toml"))
    if args.cmd in (None, "report"):
        report(cfg)
        return 0
    if args.cmd == "test":
        return test_connection(cfg)
    if args.cmd == "reset":
        reset_state()
        return 0
    if args.cmd == "check":
        send_alerts_from_state(cfg)
        poll_commands(cfg, getattr(args, "config", BASE_DIR / "config.toml"))
        return 0
    if args.cmd == "listen":
        return listen(cfg, getattr(args, "config", BASE_DIR / "config.toml"))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())