#!/usr/bin/env bash
# Installerar eller uppdaterar IPO-bevakning på en Ubuntu-server (24.04).
# Kör från repots katalog:   sudo deploy/install.sh
#
# Kan köras om: vid uppdatering (efter git pull) skrivs .env, lösenord och
# databas aldrig över. Frågar bara efter det som saknas. Kan också styras med
# miljövariabler: DOMAIN, LOGIN_USER, LOGIN_PASSWORD, NTFY_TOPIC.
set -euo pipefail

APP_DIR=/opt/ipo-bevakning
DATA_DIR=/var/lib/ipo-bevakning
ENV_FILE=$APP_DIR/.env
AUTH_FILE=/etc/caddy/ipo-auth          # användare + lösenordshash
SRC_DIR=$(cd "$(dirname "$0")/.." && pwd)

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf 'FEL: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "Kör med sudo: sudo deploy/install.sh"
[ "$SRC_DIR" = "$APP_DIR" ] || die "Repot ska ligga i $APP_DIR (nu: $SRC_DIR). Se deploy/ORACLE.md."
. /etc/os-release
[ "${ID:-}" = ubuntu ] || echo "Varning: testat för Ubuntu, detta är ${PRETTY_NAME:-okänt system}."

say "Systempaket"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv python3-pip poppler-utils sqlite3 caddy curl

say "Swap (servern har lite minne)"
if ! swapon --show | grep -q .; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "1 GB swap skapad"
else
  echo "Swap finns redan"
fi

say "Brandvägg i servern (Oracles Ubuntu blockerar allt utom SSH)"
for port in 80 443; do
  if command -v iptables >/dev/null && ! iptables -C INPUT -p tcp --dport $port -m state --state NEW -j ACCEPT 2>/dev/null; then
    iptables -I INPUT 1 -p tcp --dport $port -m state --state NEW -j ACCEPT
    echo "Öppnade port $port"
  fi
done
command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null 2>&1 || true

say "Användare och kataloger"
id ipo >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin ipo
install -d -o ipo -g ipo -m 750 "$DATA_DIR" "$DATA_DIR/backups"

say "Python-miljö"
[ -x "$APP_DIR/.venv/bin/python" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

say "Adress (domän)"
if [ -z "${DOMAIN:-}" ] && [ -f /etc/caddy/Caddyfile ] && grep -q 'ipo-bevakning' /etc/caddy/Caddyfile; then
  DOMAIN=$(grep -m1 -oE '^[a-z0-9.-]+ \{' /etc/caddy/Caddyfile | cut -d' ' -f1)
fi
if [ -z "${DOMAIN:-}" ]; then
  IP=$(curl -fsS --max-time 10 https://api.ipify.org || true)
  [ -n "$IP" ] || die "Hittade inte serverns publika IP. Kör: sudo DOMAIN=din.domän deploy/install.sh"
  DOMAIN="$(echo "$IP" | tr . -).sslip.io"
fi
echo "Appen får adressen https://$DOMAIN"

say "Inloggning"
if [ ! -s "$AUTH_FILE" ]; then
  LOGIN_USER=${LOGIN_USER:-ipo}
  if [ -z "${LOGIN_PASSWORD:-}" ]; then
    read -rsp "Välj lösenord för webbsidan (användare: $LOGIN_USER): " LOGIN_PASSWORD; echo
    [ ${#LOGIN_PASSWORD} -ge 10 ] || die "Lösenordet måste ha minst 10 tecken"
  fi
  HASH=$(caddy hash-password --plaintext "$LOGIN_PASSWORD")
  printf '%s %s\n' "$LOGIN_USER" "$HASH" > "$AUTH_FILE"
  chmod 640 "$AUTH_FILE"; chown root:caddy "$AUTH_FILE"
else
  echo "Behåller befintlig inloggning ($AUTH_FILE)"
fi
read -r AUTH_USER AUTH_HASH < "$AUTH_FILE"

say "Inställningar (.env)"
if [ ! -f "$ENV_FILE" ]; then
  if [ -z "${NTFY_TOPIC:-}" ]; then
    SUGGEST="ipo-$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
    read -rp "ntfy-ämne [Enter = $SUGGEST]: " NTFY_TOPIC
    NTFY_TOPIC=${NTFY_TOPIC:-$SUGGEST}
  fi
  sed -e "s#^IPO_DB_PATH=.*#IPO_DB_PATH=$DATA_DIR/ipo.db#" \
      -e "s#^NTFY_TOPIC=.*#NTFY_TOPIC=$NTFY_TOPIC#" \
      -e "s#^APP_BASE_URL=.*#APP_BASE_URL=https://$DOMAIN#" \
      -e "s#^IPO_SCHEDULER=.*#IPO_SCHEDULER=1#" \
      "$APP_DIR/.env.example" > "$ENV_FILE"
  echo "Skapade $ENV_FILE"
else
  echo "Behåller befintlig $ENV_FILE"
fi
chown root:ipo "$ENV_FILE"; chmod 640 "$ENV_FILE"

say "Caddy (HTTPS + lösenord)"
sed -e "s#__DOMAIN__#$DOMAIN#" -e "s#__USER__#$AUTH_USER#" -e "s#__HASH__#$AUTH_HASH#" \
    "$APP_DIR/deploy/Caddyfile.template" > /etc/caddy/Caddyfile
sed -i '1s#.*#\# Genereras av /opt/ipo-bevakning/deploy/install.sh (ipo-bevakning) – ändra i mallen.#' /etc/caddy/Caddyfile
chown root:caddy /etc/caddy/Caddyfile; chmod 640 /etc/caddy/Caddyfile  # innehåller lösenordshashen
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl enable -q caddy
systemctl reload caddy 2>/dev/null || systemctl restart caddy

say "Tjänster"
install -m 644 "$APP_DIR/deploy/ipo-bevakning.service" "$APP_DIR/deploy/ipo-backup.service" \
               "$APP_DIR/deploy/ipo-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q --now ipo-backup.timer
systemctl enable -q ipo-bevakning
systemctl restart ipo-bevakning

if [ ! -f "$DATA_DIR/ipo.db" ] || [ "$(sqlite3 "$DATA_DIR/ipo.db" 'select count(*) from ipos' 2>/dev/null || echo 0)" = 0 ]; then
  say "Första hämtningen (ca 1–2 min)"
  sudo -u ipo bash -c "cd $APP_DIR && .venv/bin/python -m app.fetch" 2>&1 | grep -vE 'Invalid Font|HTTP Request' || true
fi

sleep 2
systemctl is-active -q ipo-bevakning || die "Tjänsten startade inte – se: journalctl -u ipo-bevakning -n 50"
NTFY_NOW=$(grep -m1 '^NTFY_TOPIC=' "$ENV_FILE" | cut -d= -f2)
say "Klart!"
cat <<EOF
  Webbsida:   https://$DOMAIN   (användare: $AUTH_USER)
  ntfy-ämne:  $NTFY_NOW   ← prenumerera på detta i ntfy-appen
  Loggar:     journalctl -u ipo-bevakning -f
  Uppdatera:  cd $APP_DIR && git pull && sudo deploy/install.sh

  Första gången kan HTTPS-certifikatet ta någon minut. Tryck sedan
  "Skicka testnotis" längst ned på sidan.
EOF
