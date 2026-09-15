#!/usr/bin/env bash
#
# TeleMon - Telegram Systemüberwachung
# Installiert alle Komponenten inkl. systemd-Timer (Autostart).
#
# Verwendung:
#   sudo ./install.sh                          # Standard (Portfrei, /opt/telemon)
#   TELEMON_DIR=/srv/telemon ./install.sh      # Anderer Zielpfad
#
set -euo pipefail

TELEMON_DIR="${TELEMON_DIR:-/opt/telemon}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${SERVICE_USER:-telemon}"

say(){  printf '\033[1;36m[telemon]\033[0m %s\n' "$*"; }
good(){ printf '\033[1;32m[OK]\033[0m             %s\n' "$*"; }
fail(){ printf '\033[1;31m[FEHLER]\033[0m        %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Als root oder mit sudo ausführen."

cat <<"BANNER"
  ┌─────────────────────────────────────────────┐
  │  TeleMon - Telegram Systemüberwachung       │
  │  © 2026 Fr3akOverflow · MIT License         │
  └─────────────────────────────────────────────┘
BANNER

say "Installation nach $TELEMON_DIR"

# ---------------------------------------------------------------- Python
command -v python3 >/dev/null 2>&1 || fail "python3 fehlt (apt install python3)"
good "Python $(python3 --version | awk '{print $2}')"

# ---------------------------------------------------------------- Abhängigkeiten
say "Installiere Abhängigkeiten (psutil, requests, toml) ..."
mkdir -p "$TELEMON_DIR"
if ! python3 -c "import psutil, requests, toml" 2>/dev/null; then
    python3 -m venv "$TELEMON_DIR/.venv" 2>/dev/null || {
        apt-get update -qq
        apt-get install -y -qq python3-venv
        python3 -m venv "$TELEMON_DIR/.venv"
    }
    "$TELEMON_DIR/.venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
    PY="$TELEMON_DIR/.venv/bin/python"
else
    PY="python3"
fi
good "Python-Abhängigkeiten"

# ---------------------------------------------------------------- Installation
cp "$SCRIPT_DIR/telemon.py" "$TELEMON_DIR/telemon.py"
chmod +x "$TELEMON_DIR/telemon.py"
[ -f "$TELEMON_DIR/config.toml" ] || cp "$SCRIPT_DIR/config.toml" "$TELEMON_DIR/config.toml"
good "Dateien nach $TELEMON_DIR kopiert"

# ---------------------------------------------------------------- Konfiguration
if [ ! -f "$TELEMON_DIR/data/state.json" ]; then
    mkdir -p "$TELEMON_DIR/data"
    touch "$TELEMON_DIR/data/state.json"
fi

if grep -q "YOUR-TOKEN" "$TELEMON_DIR/config.toml"; then
    say "Bot-Zugangsdaten abfragen ..."

    cat <<GUIDE
--------------------------------------------------------------------------
  VORAUSSETZUNGEN (einmalig, in der Telegram-App auf deinem Handy):

  1. BOT-TOKEN ERZEUGEN
     - In Telegram @BotFather öffnen (Suchleiste: "BotFather")
     - Befehl /newbot eingeben
     - Namen und Username fuer den Bot vergeben (endet auf _bot)
     - Token kopieren (Format: 123456789:AA...)

  2. CHAT-ID ERMITTELN
     - Den neuen Bot ueber die Suchleiste öffnen
     - Erste Nachricht schreiben (z. B. /start)  -> wichtig!
     - Danach in Telegram @userinfobot öffnen
     - Dort erhältst du deine ID (eine reine Zahlenfolge)
       bzw. /startooo an ThePlayerKG senden (umgekehrte Eingabe)

  Mehrere Empfänger? Einfach mehrere IDs durch Komma getrennt angeben.
--------------------------------------------------------------------------
GUIDE

    read -rp "  Bot-Token (von @BotFather): " BOT_TOKEN
    read -rp "  Chat-ID(s, kommasepariert): " CHAT_IDS
    [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_IDS" ] || fail "Beide Felder sind Pflicht."

    CHAT_IDS_JSON="$(printf '%s' "$CHAT_IDS" | tr -d ' ' | sed 's/^/["/; s/,/", "/g; s/$/"]/')"
    sed -i "s|123456:YOUR-TOKEN|$BOT_TOKEN|; s|\[\"YOUR-CHAT-ID\"\]|${CHAT_IDS_JSON}|" "$TELEMON_DIR/config.toml"
    good "Bot-Zugangsdaten gespeichert (${CHAT_IDS})"
else
    good "Bot-Zugangsdaten bereits konfiguriert"
fi
chmod 600 "$TELEMON_DIR/config.toml"

# ---------------------------------------------------------------- Timer
say "Richte systemd-Timer ein ..."
INTERVAL="$(awk -F= '/seconds *=/{gsub(/ /,"",$2); print $2}' "$TELEMON_DIR/config.toml")"
INTERVAL="${INTERVAL:-300}"

cat > /etc/systemd/system/telemon.service <<EOF
[Unit]
Description=TeleMon Systemüberwachung Check

[Service]
Type=oneshot
ExecStart=$PY $TELEMON_DIR/telemon.py check --config $TELEMON_DIR/config.toml
EOF

cat > /etc/systemd/system/telemon.timer <<EOF
[Unit]
Description=TeleMon Systemüberwachung Timer

[Timer]
OnUnitActiveSec=${INTERVAL}
OnBootSec=60

[Install]
WantedBy=timers.target
EOF

cat > /usr/local/bin/telemon <<WRAPPER
#!/bin/bash
exec $PY "$TELEMON_DIR/telemon.py" "\$@"
WRAPPER
chmod +x /usr/local/bin/telemon
good "Kommando telemon installiert"

cat > /etc/systemd/system/telemon-listen.service <<EOF
[Unit]
Description=TeleMon Telegram Listener (Long-Polling)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=5
ExecStart=$PY $TELEMON_DIR/telemon.py listen --config $TELEMON_DIR/config.toml

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable telemon.timer >/dev/null
systemctl restart telemon.timer
systemctl enable telemon-listen.service >/dev/null
systemctl restart telemon-listen.service
good "Timer alle ${INTERVAL}s aktiv"
good "Listener (sofortige /status-/help-Antwort) aktiv"

# ---------------------------------------------------------------- Zusammenfassung
"$PY" "$TELEMON_DIR/telemon.py" check --config "$TELEMON_DIR/config.toml" || true
say "------------------------------------------------------------"
say "Installation abgeschlossen."
say "  Dateien     : $TELEMON_DIR"
say "  Timer       : systemctl status telemon.timer"
say "  Listener    : systemctl status telemon-listen.service"
say "  Manueller Check : $PY $TELEMON_DIR/telemon.py check --config $TELEMON_DIR/config.toml"
say "  Test Bot    : $PY $TELEMON_DIR/telemon.py test --config $TELEMON_DIR/config.toml"
say "  Logs        : journalctl -u telemon.service | -u telemon-listen.service"
say "  Deinstall   : rm -rf $TELEMON_DIR /etc/systemd/system/telemon.{service,timer,listen.service} && systemctl daemon-reload"
say "------------------------------------------------------------"
say "  © 2026 Fr3akOverflow · MIT License"
say "------------------------------------------------------------"