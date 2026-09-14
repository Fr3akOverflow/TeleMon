# TeleMon - Telegram Systemüberwachung

Leichte, agentenlose Systemüberwachung für einzelne Linux-Server.
Ressourcen-Alerts und Status direkt in deinen Telegram-Chat — ohne Prometheus, ohne Grafana, ohne Docker.

## Was macht TeleMon?

- Misst **CPU, RAM, Disk, System-Load** in konfigurierbaren Intervallen
- Schickt **nur dann** eine Telegram-Nachricht, wenn ein Schwellenwert über- oder unterschritten wird
- Erkennt **System-Reboots** automatisch
- Liefert den aktuellen Systemstatus jederzeit per `telemon report` im Terminal
- **Keine Datenbank**, kein Webserver, keine offenen Ports — nur ein Python-Skript + systemd-Timer

## Komponenten

| Datei | Funktion |
|-------|----------|
| `telemon.py` | Hauptskript: Metriken, Telegram, CLI, Listener |
| `config.toml` | Bot-Token, Chat-ID, Schwellenwerte, Interval |
| `install.sh` | Komplette Installation inkl. systemd-Timer + Listener |
| `data/state.json` | Alarm-Zustand (wird automatisch angelegt) |

## Installation (unter 2 Minuten)

```bash
git clone https://github.com/Fr3akOverflow/telemon.git
cd telemon
sudo ./install.sh
```

Das Skript zeigt beim ersten Lauf eine **Schritt-für-Schritt-Anleitung** und fragt nach:

- **Telegram Bot-Token** (von [@BotFather](https://t.me/BotFather))
- **Chat-ID(s)** — eine oder mehrere, durch Komma getrennt (z. B. über [@userinfobot](https://t.me/userinfobot) ermitteln)

Entstehende Chat-ID(s) in `config.toml`:

```toml
[telegram]
bot_token = "123456:BOT-TOKEN"
chat_ids = ["123456789", "987654321"]
```

Danach läuft der Timer automatisch alle 300 Sekunden (5 Minuten) im Hintergrund.

## Manuelle Nutzung

```bash
# Aktuellen Systemstatus im Terminal anzeigen
telemon report

# einmaliger Check + Telegram-Alert, wenn Schwellenwert verletzt
telemon check

# Bot-Verbindung testen
telemon test

# Alarm-Zustand zurücksetzen (nächster Check meldet "alles OK")
telemon reset
```

## Telegram-Befehle

Ein dauerhafter Listener (systemd-Dienst `telemon-listen.service`) wartet rund um
die Uhr auf Nachrichten und antwortet **sofort**.

Jeder autorisierte Empfänger (in `chat_ids`) kann Systemstatus abfragen **und
Einstellungen per Telegram ändern**:

| Befehl | Funktion |
|--------|----------|
| `/status` | aktueller Systemstatus |
| `/help` | Befehlsübersicht |
| `/settings` | Einstellungen anzeigen |
| `/set cpu 80` | CPU-Schwelle ändern (`cpu`, `mem`, `disk`, `load`, `interval`) |
| `/clients` | Liste der Empfänger |
| `/addclient 123456` | neuen Empfänger hinzufügen |
| `/delclient 123456` | Empfänger entfernen |

Beispiel:
```
/set cpu 80        → ✅ cpu auf 80 gesetzt.
/set interval 600  → ✅ interval auf 600 gesetzt. systemd-Timer wird automatisch neu geladen.
/addclient 123456  → fügt hinzu + schickt Willkommensnachricht
```

`/set interval` schreibt den neuen Wert nicht nur in `config.toml`, sondern
aktualisiert auch den systemd-Timer (`telemon.timer`) und startet ihn neu —
ohne Neustart des Dienstes.

Die Befehle funktionieren auch, wenn der Listener einmal ausfällt: Der
Timer-Check verarbeitet versäumte Kommandos beim nächsten Lauf nach.

## Konfiguration (config.toml)

```toml
[telegram]
bot_token = "123456:BOT-TOKEN"
chat_ids = ["123456789", "987654321"]

[thresholds]
cpu_percent = 90.0
memory_percent = 90.0
disk_percent = 85.0
load1 = 4.0

[interval]
seconds = 300
```

Nach Änderungen an `chat_ids`/`thresholds` einfach `systemctl restart telemon-listen.service`.
Für `interval.seconds` wird `telemon.timer` automatisch aktualisiert (auch per `/set interval`).
Manueller Timer-Reset: `sudo systemctl restart telemon.timer`

## Telegram-Nachricht

```
🖥 hostname
⏱ Uptime: 3d 14h 22m
CPU   : 46.4% (Load 1.90)
RAM   : 10.3% (840 MB / 8194 MB)
Disk  : 35.8% (4835 MB frei)
⬆️  up   : 468 MB
⬇️  down : 1054 MB

⚠️ CPU 95.0% ≥ 90%
```

Bei Normalisierung:
```
✅ Alle Werte im Rahmen
```

## Requirements

- Linux mit Python >= 3.10
- Root-Rechte (systemd-Timer)
- Keine weiteren Abhängigkeiten (psutil, requests, toml)

## Logs prüfen

```bash
journalctl -u telemon.service --since "10 min ago"
systemctl status telemon.timer
```

## Deinstallation

```bash
sudo rm -rf /opt/telemon /etc/systemd/system/telemon.{service,timer}
sudo systemctl daemon-reload
```

## Lizenz

MIT License
