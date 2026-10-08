#!/usr/bin/env bash
# Install (or update) Breakout on a fresh Ubuntu/Debian server, behind HTTPS.
#
#   curl -fsSL https://raw.githubusercontent.com/crisonmathew-del/Updated-stock/claude/vibrant-darwin-yeisdw/infra/install.sh | sudo bash
#
# It installs Docker if needed, downloads the app to /opt/breakout, asks for your domain, an
# email for the HTTPS certificate, your login and the SEC contact, writes .env (with generated
# secrets), builds and starts everything, and creates your login. Running it again updates the
# app to the latest version and keeps your settings and data.
#
# Answers can also come from the environment (no questions asked): BREAKOUT_DOMAIN,
# BREAKOUT_ACME_EMAIL, BREAKOUT_LOGIN_EMAIL, BREAKOUT_PASSWORD, BREAKOUT_SEC_EMAIL.
# Other options: BREAKOUT_DIR (default /opt/breakout), BREAKOUT_BRANCH (default: the
# repository's default branch), BREAKOUT_REPO. `--prepare-only` stops after writing .env.
set -euo pipefail

REPO="${BREAKOUT_REPO:-https://github.com/crisonmathew-del/Updated-stock.git}"
DIR="${BREAKOUT_DIR:-/opt/breakout}"
BRANCH="${BREAKOUT_BRANCH:-}"
PREPARE_ONLY=0
[ "${1:-}" = "--prepare-only" ] && PREPARE_ONLY=1
MIN_PASSWORD_LENGTH=12

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# Questions go to the terminal even when this script is piped from curl.
ask() { # ask VAR "Question" [default]
  local var=$1 question=$2 default=${3:-} answer
  if [ -n "${!var:-}" ]; then return; fi
  [ -r /dev/tty ] || fail "$var is not set and there is no terminal to ask for it."
  if [ -n "$default" ]; then question="$question [$default]"; fi
  read -r -p "$question: " answer </dev/tty
  answer=${answer:-$default}
  [ -n "$answer" ] || fail "$question: an answer is needed."
  printf -v "$var" '%s' "$answer"
}

ask_password() {
  if [ -n "${BREAKOUT_PASSWORD:-}" ]; then return; fi
  [ -r /dev/tty ] || fail "BREAKOUT_PASSWORD is not set and there is no terminal to ask for it."
  local first second
  while true; do
    read -r -s -p "Password for your login (at least $MIN_PASSWORD_LENGTH characters): " first </dev/tty
    echo
    read -r -s -p "Repeat it: " second </dev/tty
    echo
    if [ "${#first}" -lt "$MIN_PASSWORD_LENGTH" ]; then
      echo "Too short: use at least $MIN_PASSWORD_LENGTH characters."
    elif [ "$first" != "$second" ]; then
      echo "The two didn't match. Try again."
    else
      BREAKOUT_PASSWORD=$first
      return
    fi
  done
}

# Replace KEY=... in .env (the value is written as given; sed-special characters escaped).
set_env() {
  local key=$1 value=$2 escaped
  escaped=$(printf '%s' "$value" | sed -e 's/[\\|&]/\\&/g')
  if grep -q "^$key=" .env; then
    sed -i "s|^$key=.*|$key=$escaped|" .env
  else
    printf '%s=%s\n' "$key" "$value" >>.env
  fi
}

# Everything runs from main, called on the last line: piped from curl, bash then has the whole
# script before any of it runs, so a command that reads stdin can't swallow the rest of it.
main() {
  [ "$(id -u)" -eq 0 ] || fail "Run it as root: put sudo in front (curl ... | sudo bash)."
  command -v apt-get >/dev/null || fail "This installer supports Ubuntu and Debian (apt). See docs/deploy.md for other systems."

  say "1/5  Checking the tools (git, make, curl, openssl, Docker)"
  missing=()
  for tool in git make curl openssl; do command -v "$tool" >/dev/null || missing+=("$tool"); done
  if [ "${#missing[@]}" -gt 0 ]; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates "${missing[@]}"
  fi
  if ! command -v docker >/dev/null; then
    echo "Installing Docker (this takes a minute)..."
    curl -fsSL https://get.docker.com | sh
  fi
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is missing (docker compose version failed)."

  # The first full market scan peaks at about 3 GB on top of the services' ~2 GB. On a server
  # with less than ~7 GB of memory, a swap file lets it finish (slower) instead of failing.
  mem_mb=$(awk '/^MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
  swap_mb=$(awk '/^SwapTotal/ {print int($2 / 1024)}' /proc/meminfo)
  add_swap() {
    { fallocate -l 4G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none; } &&
      chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile &&
      { grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab; } &&
      echo 'vm.swappiness=10' >/etc/sysctl.d/99-breakout-swap.conf &&
      sysctl -q -w vm.swappiness=10
  }
  if [ "$mem_mb" -lt 7000 ] && [ "$swap_mb" -lt 1024 ] && [ ! -e /swapfile ]; then
    echo "This server has ${mem_mb} MB of memory: adding a 4 GB swap file for the big scans."
    add_swap || echo "Couldn't add a swap file (continuing without it; the first full scan may run out of memory)."
  fi

  say "2/5  Downloading the app to $DIR"
  if [ -d "$DIR/.git" ]; then
    git -C "$DIR" fetch --quiet origin
    current=$(git -C "$DIR" rev-parse --abbrev-ref HEAD)
    git -C "$DIR" checkout --quiet "${BRANCH:-$current}"
    git -C "$DIR" pull --quiet --ff-only
  else
    git clone --quiet ${BRANCH:+--branch "$BRANCH"} "$REPO" "$DIR"
  fi
  cd "$DIR"
  echo "Version: $(git log -1 --format='%h %s')"

  say "3/5  Settings (.env)"
  first_install=0
  if [ -f .env ]; then
    echo "Keeping the existing .env (edit $DIR/.env to change settings, then run this again)."
  else
    first_install=1
    ask BREAKOUT_DOMAIN "Your domain (its DNS A record must point at this server), e.g. stocks.example.com"
    ask BREAKOUT_ACME_EMAIL "Email for the HTTPS certificate (Let's Encrypt writes only about problems)"
    ask BREAKOUT_SEC_EMAIL "Contact email the SEC requires on data requests" "$BREAKOUT_ACME_EMAIL"
    cp .env.example .env
    chmod 600 .env
    set_env SESSION_SECRET "$(openssl rand -hex 32)"
    set_env POSTGRES_PASSWORD "$(openssl rand -hex 24)"
    set_env DOMAIN "$BREAKOUT_DOMAIN"
    set_env ACME_EMAIL "$BREAKOUT_ACME_EMAIL"
    set_env SEC_USER_AGENT "\"Breakout $BREAKOUT_SEC_EMAIL\""
    echo "Wrote $DIR/.env with new secrets (keep it private; it is never uploaded anywhere)."
  fi
  domain=$(sed -n 's/^DOMAIN=//p' .env)
  [ -n "$domain" ] || fail "DOMAIN is empty in $DIR/.env."

  if [ "$PREPARE_ONLY" -eq 1 ]; then
    say "Prepared $DIR/.env. Review it, then run this installer again (without --prepare-only)."
    exit 0
  fi

  # The HTTPS certificate needs the domain to point here; check before Let's Encrypt is asked.
  if [ "$domain" != "localhost" ]; then
    here=$(curl -fsS --max-time 10 https://api.ipify.org || true)
    there=$(getent ahostsv4 "$domain" | awk 'NR == 1 {print $1}' || true)
    if [ -n "$here" ] && [ "$there" != "$here" ]; then
      echo "Warning: $domain points at '${there:-nothing}', but this server is $here."
      echo "The HTTPS certificate can't be issued until the DNS A record points here (it can take"
      echo "a few minutes after you change it). The app will retry; you can also continue now."
      if [ -r /dev/tty ]; then
        read -r -p "Continue anyway? [y/N] " go </dev/tty
        [ "$go" = y ] || [ "$go" = Y ] || fail "Stopped. Fix the DNS record, then run this again."
      fi
    fi
  fi

  say "4/5  Building and starting (the first time takes 5-10 minutes)"
  make deploy

  say "5/5  Your login"
  if [ "$first_install" -eq 1 ] || [ -n "${BREAKOUT_LOGIN_EMAIL:-}" ]; then
    ask BREAKOUT_LOGIN_EMAIL "Email you'll sign in with"
    ask_password
    if docker compose -f infra/docker-compose.prod.yml --env-file .env run --rm -T \
      -e BREAKOUT_PASSWORD="$BREAKOUT_PASSWORD" api \
      python -m app.cli create-user --email "$BREAKOUT_LOGIN_EMAIL" </dev/null; then
      :
    else
      echo "(If it says the user already exists, sign in with your existing password.)"
    fi
  else
    echo "Keeping the existing login (change the password with:"
    echo "  cd $DIR && make prod-cli cmd=\"set-password --email you@example.com\")"
  fi

  say "Breakout is running at https://$domain"
  cat <<EOF

What happens next:
  - The market loads by itself: the stock list, then 10 years of daily prices (about 30-60
    minutes). Watch it under Admin -> Data. Company financials load the first night.
  - Admin -> Status shows every service; the database is backed up every night at 02:30 ET.

Useful commands (run in $DIR):
  make prod-ps            what's running
  make prod-logs          follow the logs (Ctrl+C to stop)
  make backup-now         back up now
  sudo bash infra/install.sh   update to the latest version (keeps settings and data)
EOF
}

main "$@"
