#!/usr/bin/env bash
# Installer for the bot-seller bot (Bot 1)
# Usage:  bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USER/YOUR_REPO/main/install.sh)
set -Eeuo pipefail

# ⚠️ قبل از انتشار آدرس مخزن خودتان را اینجا بگذارید (یا موقع اجرا REPO_URL را بدهید)
REPO_URL="${REPO_URL:-https://github.com/YOUR_USER/YOUR_REPO.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/seller-bot}"
SERVICE_NAME="${SERVICE_NAME:-seller-bot}"
BOT_USER="${BOT_USER:-sellerbot}"
CLI_NAME="${CLI_NAME:-sellerbot}"
ENV_FILE="$INSTALL_DIR/.env"

if [[ -t 1 ]]; then
  R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[36m'; N=$'\033[0m'
else
  R=""; G=""; Y=""; B=""; N=""
fi
info() { printf '%s[•]%s %s\n' "$B" "$N" "$*"; }
ok()   { printf '%s[✔]%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s[!]%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%s[✘]%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
trap 'die "Error on line $LINENO — installation aborted."' ERR

[[ -r /dev/tty ]] || die "This is an interactive script and requires a terminal."

trim() { printf '%s' "$1" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'; }

ask() {
  local __var="$1" __prompt="$2" __def="${3:-}" __req="${4:-0}" __val=""
  while true; do
    if [[ -n "$__def" ]]; then
      printf '%s [%s]: ' "$__prompt" "$__def" >&2
    else
      printf '%s: ' "$__prompt" >&2
    fi
    IFS= read -r __val </dev/tty || die "Failed to read input."
    __val="$(trim "$__val")"
    __val="${__val:-$__def}"
    if [[ -z "$__val" && "$__req" == "1" ]]; then
      warn "This field is required."
      continue
    fi
    break
  done
  printf -v "$__var" '%s' "$__val"
}

ask_yn() {
  local p="$1" d="${2:-Y}" a="" hint="[Y/n]"
  if [[ "$d" == "N" ]]; then hint="[y/N]"; fi
  printf '%s %s: ' "$p" "$hint" >&2
  IFS= read -r a </dev/tty || die "Failed to read input."
  a="$(trim "$a")"
  a="${a:-$d}"
  [[ "$a" =~ ^[Yy] ]]
}

ask_number() {  # ask_number VAR "prompt" default min max
  local __var="$1" __prompt="$2" __def="$3" __min="$4" __max="$5" __in=""
  while true; do
    ask __in "$__prompt" "$__def" 1
    if [[ "$__in" =~ ^[0-9]+$ ]] && (( __in >= __min && __in <= __max )); then break; fi
    warn "Enter a number between $__min and $__max."
  done
  printf -v "$__var" '%s' "$__in"
}

ENV_BUF=""
env_quote() {
  local v="$1"
  v="${v//\\/\\\\}"
  v="${v//\'/\\\'}"
  printf "'%s'" "$v"
}
put() {
  [[ -n "${2:-}" ]] || return 0
  ENV_BUF+="$1=$(env_quote "$2")"$'\n'
}

check_root() {
  [[ $EUID -eq 0 ]] || die "Please run as root (e.g., sudo -i)."
  command -v systemctl >/dev/null 2>&1 || die "This script requires systemd."
}

install_packages() {
  info "Installing system prerequisites..."
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y -qq
    apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q git curl ca-certificates python3 python3-pip
  elif command -v yum >/dev/null 2>&1; then
    yum install -y -q git curl ca-certificates python3 python3-pip
  else
    die "Unsupported package manager (apt / dnf / yum). Please install prerequisites manually."
  fi
}

PYTHON_BIN=""
find_python() {
  local p
  for p in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$p" >/dev/null 2>&1 \
       && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
      PYTHON_BIN="$(command -v "$p")"
      return 0
    fi
  done
  die "Python 3.10 or higher is required (recommended: Ubuntu 22.04+ or Debian 12+)."
}

GIT() { git -c "safe.directory=$INSTALL_DIR" "$@"; }

fetch_code() {
  [[ "$REPO_URL" != *YOUR_USER* ]] || die "Set REPO_URL first (edit the top of this script, or run: REPO_URL=https://github.com/you/repo.git bash install.sh)."
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Updating bot source (.env and database are preserved)..."
    GIT -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
    GIT -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
  else
    if [[ -e "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
      die "Directory $INSTALL_DIR is not empty and is not a bot repository."
    fi
    info "Cloning bot source from GitHub..."
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi
}

ensure_user() {
  if ! id -u "$BOT_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$INSTALL_DIR" --shell "$(command -v nologin || echo /usr/sbin/nologin)" "$BOT_USER"
  fi
}

configure_env() {
  if [[ -f "$ENV_FILE" ]]; then
    if ask_yn "A .env file already exists. Use the existing one?" Y; then
      ok "Using previous configuration."
      return 0
    fi
    cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%s)"
    ok "Previous .env backed up."
  fi

  local BOT_TOKEN="" BOT_USERNAME="" ADMINS_RAW="" resp=""

  printf '\n%s━━━━━━━━ Main Settings ━━━━━━━━%s\n' "$B" "$N"

  while true; do
    ask BOT_TOKEN "Seller bot token (from @BotFather)" "" 1
    if [[ ! "$BOT_TOKEN" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]]; then
      warn "Invalid token format. Example: 123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      continue
    fi
    resp="$(curl -sS --max-time 10 "https://api.telegram.org/bot${BOT_TOKEN}/getMe" 2>/dev/null || true)"
    if [[ "$resp" == *'"ok":true'* ]]; then
      BOT_USERNAME="$(printf '%s' "$resp" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')"
      ok "Token is valid. Bot: @${BOT_USERNAME}"
      break
    elif [[ "$resp" == *'"ok":false'* ]]; then
      warn "Telegram rejected this token. Please enter again."
    else
      warn "Could not verify token (connection to api.telegram.org failed)."
      if ask_yn "Continue without verification?" N; then break; fi
    fi
  done

  while true; do
    ask ADMINS_RAW "Admin numeric IDs (comma-separated)" "" 1
    ADMINS_RAW="${ADMINS_RAW//[[:space:]]/}"
    if [[ "$ADMINS_RAW" =~ ^[0-9]+(,[0-9]+)*$ ]]; then break; fi
    warn "Only numbers and commas allowed. Example: 123456789,987654321 (get numeric ID from @userinfobot)"
  done

  put BOT_TOKEN "$BOT_TOKEN"
  put ADMIN_USER_IDS "$ADMINS_RAW"

  configure_advanced

  umask 077
  printf '%s' "$ENV_BUF" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok ".env file created."
}

configure_advanced() {
  local DBP="seller.sqlite3" MINT="10000" MAXT="100000000" REM="3"
  printf '\n%s━━━━━━━━ Advanced Settings ━━━━━━━━%s\n' "$B" "$N"
  echo "Store name, card number, support username and service prices are set later from the bot's /admin panel."
  if ! ask_yn "Change database path / top-up limits / expiry reminder now?" N; then
    return 0
  fi
  ask DBP "Database file path (relative to $INSTALL_DIR)" "$DBP" 1
  ask_number MINT "Minimum top-up amount (Toman)" "$MINT" 1 1000000000
  ask_number MAXT "Maximum top-up amount (Toman)" "$MAXT" "$MINT" 100000000000
  ask_number REM "Days before expiry to remind customers" "$REM" 0 365
  put DATABASE_PATH "$DBP"
  put MIN_TOPUP "$MINT"
  put MAX_TOPUP "$MAXT"
  put EXPIRY_REMINDER_DAYS "$REM"
}

setup_venv() {
  info "Creating Python virtual environment and installing dependencies (this may take a few minutes)..."
  if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
  fi
  "$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
  "$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
}

install_service() {
  info "Creating systemd service..."
  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Telegram Bot Seller (Bot 1)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${BOT_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python bot.py
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
  chown -R "$BOT_USER":"$BOT_USER" "$INSTALL_DIR"
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
  systemctl restart "$SERVICE_NAME"
}

install_cli() {
  cat > "/usr/local/bin/${CLI_NAME}" <<EOF
#!/usr/bin/env bash
SVC="${SERVICE_NAME}"; DIR="${INSTALL_DIR}"; BR="${BRANCH}"; BU="${BOT_USER}"; ME="${CLI_NAME}"
[[ \$EUID -eq 0 ]] || { echo "Run as root."; exit 1; }
case "\${1:-help}" in
  start|stop|restart) systemctl "\$1" "\$SVC" && echo "OK: \$1" ;;
  status)  systemctl status "\$SVC" --no-pager ;;
  logs)    journalctl -u "\$SVC" -f -n 100 --no-pager ;;
  edit)    "\${EDITOR:-\$(command -v nano || echo vi)}" "\$DIR/.env" && systemctl restart "\$SVC" && echo "Bot restarted with new settings." ;;
  update)
    git -c safe.directory="\$DIR" -C "\$DIR" fetch --depth 1 origin "\$BR"
    git -c safe.directory="\$DIR" -C "\$DIR" reset --hard "origin/\$BR"
    "\$DIR/venv/bin/pip" install -q -r "\$DIR/requirements.txt"
    chown -R "\$BU":"\$BU" "\$DIR"
    systemctl restart "\$SVC" && echo "Update completed." ;;
  uninstall)
    read -r -p "Remove bot and service? [y/N] " a
    [[ "\$a" =~ ^[Yy] ]] || exit 0
    systemctl disable --now "\$SVC" 2>/dev/null || true
    rm -f "/etc/systemd/system/\$SVC.service" "/usr/local/bin/\$ME"; systemctl daemon-reload
    read -r -p "Also delete \$DIR (including .env and database)? [y/N] " b
    if [[ "\$b" =~ ^[Yy] ]]; then rm -rf "\$DIR"; fi
    echo "Removed." ;;
  *) echo "Usage: \$ME {start|stop|restart|status|logs|edit|update|uninstall}" ;;
esac
EOF
  chmod +x "/usr/local/bin/${CLI_NAME}"
}

finish() {
  sleep 5
  echo
  if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Bot installed and running successfully 🎉"
  else
    warn "Service failed to start. Last logs:"
    journalctl -u "$SERVICE_NAME" -n 25 --no-pager || true
    echo
    warn "After fixing the issue (e.g., with: ${CLI_NAME} edit), try again."
  fi
  cat <<EOF

Management commands:
  ${CLI_NAME} status      Bot status
  ${CLI_NAME} logs        View live logs
  ${CLI_NAME} restart     Restart
  ${CLI_NAME} edit        Edit settings (.env)
  ${CLI_NAME} update      Update to latest version
  ${CLI_NAME} uninstall   Uninstall

Install path: ${INSTALL_DIR}
Next: send /start to the bot in Telegram, then /admin to add services, card number and support username.
EOF
}

main() {
  printf '%s\n  Telegram Bot Seller Installer\n%s\n' "$B" "$N"
  check_root
  install_packages
  find_python
  ensure_user
  fetch_code
  configure_env
  setup_venv
  install_service
  install_cli
  finish
}

main "$@"
