#!/usr/bin/env bash
# Update OpenEMS PCB Studio from git and restart the service.
#
#   ./scripts/update.sh            # refuses while a simulation is running
#   ./scripts/update.sh --force    # restart anyway (kills a running sim)
#   ./scripts/update.sh --stash    # stash local changes, update, restore
#
# Steps: safety checks -> git pull --ff-only -> reinstall dependencies if
# requirements.txt changed -> restart the service -> health check -> fast
# tests. Nothing under sims/ or projects/ is touched (both gitignored).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
PYTHON="${PYTHON:-$(command -v python3)}"
PORT="${PORT:-8036}"
FORCE=0
STASH=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --stash) STASH=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) printf 'unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

die()  { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[33mnote:\033[0m %s\n' "$1"; }

svc_active() { systemctl --user is-active --quiet openems-webgui 2>/dev/null; }

# ---- 1. never interrupt a running simulation --------------------------
STATE="$(curl -fsS --max-time 3 "http://localhost:$PORT/api/status" 2>/dev/null \
         | "$PYTHON" -c 'import json,sys
try: print(json.load(sys.stdin).get("state","?"))
except Exception: print("?")' 2>/dev/null || echo offline)"
case "$STATE" in
  starting|running|post)
    if [ "$FORCE" -eq 0 ]; then
      die "a simulation is $STATE right now - updating would restart the server and kill it.
Wait for it to finish (watch the Run tab), or re-run with --force to update anyway."
    fi
    warn "a simulation is $STATE and --force was given: it will be killed"
    ;;
  offline) info "server is not responding on port $PORT (nothing to interrupt)" ;;
  *)       info "server state: $STATE" ;;
esac

# ---- 2. working tree must be clean (or stashed) -----------------------
if [ -n "$(git status --porcelain)" ]; then
  if [ "$STASH" -eq 1 ]; then
    info "stashing local changes"
    git stash push -u -m "update.sh $(date -Iseconds)" >/dev/null
    STASHED=1
  else
    git status --short
    die "local changes present. Commit them, or re-run with --stash to set them aside."
  fi
fi

OLD="$(git rev-parse HEAD)"
REQ_OLD="$(git hash-object requirements.txt 2>/dev/null || echo none)"

# ---- 3. pull ----------------------------------------------------------
info "fetching from $(git remote get-url origin 2>/dev/null || echo '(no remote)')"
git fetch --prune
if ! git merge-base --is-ancestor HEAD "@{u}" 2>/dev/null; then
  warn "local branch has commits that are not on the remote - pulling anyway (fast-forward only)"
fi
git pull --ff-only || die "fast-forward pull failed.
Your branch has diverged from the remote; resolve it manually (git log --oneline HEAD @{u})."
NEW="$(git rev-parse HEAD)"

if [ "$OLD" = "$NEW" ]; then
  info "already up to date ($(git rev-parse --short HEAD))"
else
  info "updated $(git rev-parse --short "$OLD") -> $(git rev-parse --short "$NEW")"
  git --no-pager log --oneline "$OLD..$NEW" | sed 's/^/    /'
fi

# ---- 4. dependencies --------------------------------------------------
if [ "$REQ_OLD" != "$(git hash-object requirements.txt 2>/dev/null || echo none)" ]; then
  info "requirements.txt changed - installing dependencies"
  # Distro pythons are PEP 668 "externally managed" and refuse a plain
  # install; retry into the user site, which the service python still sees.
  "$PYTHON" -m pip install -q -r requirements.txt \
    || "$PYTHON" -m pip install -q --user --break-system-packages -r requirements.txt \
    || warn "dependency install failed - check manually"
fi

# ---- 5. restart -------------------------------------------------------
if svc_active; then
  info "restarting the service"
  systemctl --user restart openems-webgui
  for _ in $(seq 1 25); do
    curl -fsS --max-time 2 "http://localhost:$PORT/api/status" >/dev/null 2>&1 && break
    sleep 0.4
  done
  if curl -fsS --max-time 2 "http://localhost:$PORT/api/status" >/dev/null 2>&1; then
    info "service healthy on http://localhost:$PORT"
  else
    printf '\033[31merror:\033[0m service did not come back up.\n' >&2
    journalctl --user -u openems-webgui -n 25 --no-pager >&2 || true
    exit 1
  fi
elif systemctl --user list-unit-files openems-webgui.service >/dev/null 2>&1 \
     && systemctl --user cat openems-webgui >/dev/null 2>&1; then
  warn "the service is installed but not running - start it with:
  systemctl --user start openems-webgui"
else
  warn "the service is not installed - install it with:
  ./scripts/install-service.sh"
fi

# ---- 6. restore stash + smoke test ------------------------------------
if [ "${STASHED:-0}" -eq 1 ]; then
  info "restoring stashed changes"
  git stash pop || warn "stash pop hit conflicts - resolve them (git stash list)"
fi

info "running the fast test suite"
if "$PYTHON" -m pytest tests/ -q 2>&1 | tail -3; then
  printf '\033[32m==> update complete\033[0m\n'
else
  warn "tests reported failures - see the output above"
fi
