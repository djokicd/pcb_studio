#!/usr/bin/env bash
# Install (or re-install) OpenEMS PCB Studio as a systemd --user service.
#
# The server then runs independently of any terminal, browser or SSH
# session, which is what keeps long simulations alive. Re-running this
# script is safe: it rewrites the unit and restarts the service.
#
#   ./scripts/install-service.sh              # port 8036
#   PORT=9000 ./scripts/install-service.sh    # different port
#   PYTHON=/usr/bin/python3.12 ./scripts/install-service.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$(command -v python3 || true)}"
PORT="${PORT:-8036}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/openems-webgui.service"
TEMPLATE="$REPO/openems-webgui.service"

die() { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$1"; }

[ -n "$PYTHON" ] || die "python3 not found (set PYTHON=/path/to/python3)"
[ -f "$REPO/server.py" ] || die "server.py not found in $REPO"
[ -f "$TEMPLATE" ] || die "unit template missing: $TEMPLATE"
command -v systemctl >/dev/null || die "systemctl not found - this system does not use systemd.
Run the server manually instead:  cd $REPO && $PYTHON server.py"

# flask is imported at startup; fail here rather than in a crash loop
"$PYTHON" -c 'import flask' 2>/dev/null || die "flask is not installed for $PYTHON.
Install the dependencies first:  $PYTHON -m pip install -r $REPO/requirements.txt"

info "repository : $REPO"
info "python     : $PYTHON"
info "port       : $PORT"

mkdir -p "$UNIT_DIR"
sed -e "s|@REPO@|$REPO|g" \
    -e "s|@PYTHON@|$PYTHON|g" \
    -e "s|@PORT@|$PORT|g" \
    "$TEMPLATE" > "$UNIT"
info "unit written: $UNIT"

systemctl --user daemon-reload
systemctl --user enable openems-webgui >/dev/null
systemctl --user restart openems-webgui
info "service enabled and (re)started"

# keep the service running while logged out - the point of the exercise
if command -v loginctl >/dev/null; then
  if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" != "yes" ]; then
    info "enabling linger so the service survives logout"
    loginctl enable-linger "$USER" 2>/dev/null \
      || printf '\033[33mnote:\033[0m could not enable linger automatically. Run:\n  sudo loginctl enable-linger %s\n' "$USER"
  fi
fi

# wait for the HTTP endpoint to answer before declaring success
for _ in $(seq 1 25); do
  if curl -fsS --max-time 2 "http://localhost:$PORT/api/status" >/dev/null 2>&1; then
    printf '\033[32m==> ready:\033[0m http://localhost:%s\n' "$PORT"
    exit 0
  fi
  sleep 0.4
done

printf '\033[31merror:\033[0m the service did not answer on port %s.\n' "$PORT" >&2
echo "Recent log:" >&2
journalctl --user -u openems-webgui -n 25 --no-pager >&2 || true
exit 1
