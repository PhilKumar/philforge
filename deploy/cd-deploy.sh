#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  PhilForge — Safe Blue-Green Deployment
# ═══════════════════════════════════════════════════════════════
#  Called by GitHub Actions after `git pull`.
#  Starts new code on a standby port, health-checks it,
#  restores engine ownership, then swaps nginx. A short cutover gap is
#  intentional: live trade execution must never overlap across workers.
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

APP_DIR="${PHILFORGE_APP_DIR:-/home/ec2-user/philforge}"
if [[ ! -d "$APP_DIR" ]]; then
    echo "[DEPLOY] ERROR: PhilForge app directory not found: $APP_DIR"
    exit 1
fi

APP="philforge"

VENV="$APP_DIR/venv"

BLUE_PORT=8000
GREEN_PORT=8001
PORT_FILE="$HOME/.${APP}-active-port"
UPSTREAM_CONF="/etc/nginx/conf.d/${APP}-upstream.conf"

HEALTH_PATH="/api/health"
HEALTH_TIMEOUT=180         # seconds to wait for standby health
SYNC_SITE_CONFIG="${SYNC_SITE_CONFIG:-0}"  # set to 1 only when intentionally replacing the server vhost
LOCK_FILE="$HOME/.${APP}-deploy.lock"

LOG_TAG="[DEPLOY]"

# ── Helpers ───────────────────────────────────────────────────
log()  { echo "$LOG_TAG $(date '+%H:%M:%S') $*"; }
die()  { log "ERROR: $*"; exit 1; }

# Serialize all deploy/cutover activity on the server. This prevents a manual
# rollout and the GitHub Actions deploy workflow from swapping/stopping ports
# at the same time. rollout-main.sh already owns this lock, so it explicitly
# marks the child invocation and avoids waiting on its own parent forever.
if [[ "${PHILFORGE_DEPLOY_LOCK_HELD:-0}" != "1" ]]; then
    exec 9>"$LOCK_FILE"
    flock 9
fi

health_check() {
    local port=$1
    for i in $(seq 1 "$HEALTH_TIMEOUT"); do
        if curl -sf --max-time 3 "http://127.0.0.1:${port}${HEALTH_PATH}" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# ── Determine active/standby ─────────────────────────────────
if [[ -f "$PORT_FILE" ]]; then
    ACTIVE_PORT=$(cat "$PORT_FILE")
else
    # First deploy — assume blue
    ACTIVE_PORT=$BLUE_PORT
    echo "$ACTIVE_PORT" > "$PORT_FILE"
fi

if [[ "$ACTIVE_PORT" == "$BLUE_PORT" ]]; then
    STANDBY_PORT=$GREEN_PORT
else
    STANDBY_PORT=$BLUE_PORT
fi

log "Active: port $ACTIVE_PORT → Deploying to: port $STANDBY_PORT"

# ── 1. Install dependencies + clear stale bytecode ───────────
log "Installing dependencies..."
source "$VENV/bin/activate"
pip install -q --disable-pip-version-check -r "$APP_DIR/requirements.txt"

# Keep the template unit in sync before the standby starts. In particular, the
# current unit binds Uvicorn to loopback and provides its instance port so the
# application can defer engine restore while it is the standby.
log "Refreshing systemd template..."
sudo cp "$APP_DIR/deploy/philforge.service" "/etc/systemd/system/${APP}@.service"
sudo cp "$APP_DIR/deploy/philforge-backup.service" "/etc/systemd/system/${APP}-backup.service"
sudo cp "$APP_DIR/deploy/philforge-backup.timer" "/etc/systemd/system/${APP}-backup.timer"
sudo systemctl daemon-reload
sudo systemctl enable --now "${APP}-backup.timer"

log "Clearing __pycache__ to prevent stale bytecode..."
find "$APP_DIR" -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# ── 1b. NOT DURING THE SESSION, NOT WHILE AN ENGINE IS RUNNING ──
# Phil, 2026-08-17: a deploy at 09:58 IST ended a paper trade that was going
# fine. The engines survive a restart now, but a blue-green cutover still puts a
# gap in the middle of a live session, and a trade being managed is not
# something to interrupt for a chart label. So: refuse, unless told otherwise.
#
#   FORCE_DEPLOY=1  deploy anyway (say why in the commit or to Phil first)
#
# "Running" is read from the engines' own state files rather than from an HTTP
# call, because it must be answerable even when the app is wedged.
IST_NOW="$(TZ=Asia/Kolkata date '+%H%M')"
IST_DOW="$(TZ=Asia/Kolkata date '+%u')"          # 1=Mon .. 7=Sun
STATE_ROOT="${PHILFORGE_USER_DATA_ROOT:-$APP_DIR/user_data}"

engines_running() {
    # A state file that says running AND carries today's session date.
    #
    # THIS RUNS UNDER `set -euo pipefail`. On its first live run (2026-08-17
    # 14:55 IST) $STATE_ROOT did not exist on the box, `find` exited 1, the
    # pipeline failed, the `BUSY="$(...)"` capture failed, and the deploy died
    # with NO message -- before either "REFUSING" or "no engine is running"
    # could print -- while the checkout had already moved to the new commit
    # (front end new, engine old). A missing state root, an unreadable dir or
    # a last file that simply does not match must all read as "nothing
    # running", which is the truthful answer, not as a crash.
    local today
    today="$(TZ=Asia/Kolkata date '+%Y-%m-%d')"
    [[ -d "$STATE_ROOT" ]] || return 0
    { find "$STATE_ROOT" -maxdepth 3 \( -name 'live_state_*.json' -o -name 'paper_state_*.json' \) 2>/dev/null || true; } \
      | while read -r f; do
            grep -q '"running": *true' "$f" 2>/dev/null || continue
            grep -q "\"session_date\": *\"$today\"" "$f" 2>/dev/null || continue
            echo "$f"
        done
    return 0
}

if [[ "${FORCE_DEPLOY:-0}" != "1" && "$IST_DOW" -le 5 && "$IST_NOW" > "0915" && "$IST_NOW" < "1530" ]]; then
    BUSY="$(engines_running | head -3)"
    if [[ -n "$BUSY" ]]; then
        log "✋ REFUSING TO DEPLOY — NSE session is open (IST $IST_NOW) and these engines are running:"
        printf '%s\n' "$BUSY" | while read -r f; do log "     $(basename "$f")"; done
        log "     A deploy restarts them mid-session. Wait for 15:30 IST, or re-run with FORCE_DEPLOY=1."
        exit 78
    fi
    log "NSE session is open (IST $IST_NOW) but no engine is running — deploying."
fi

# ── 2. Stop standby if somehow still running ──────────────────
sudo systemctl stop "${APP}@${STANDBY_PORT}" 2>/dev/null || true

# ── 2a. Tell active instance to persist state before restart ──
# The standby remains passive; it must not restore these engines until this
# active instance has fully stopped.
if curl -sf --max-time 5 -X POST "http://127.0.0.1:${ACTIVE_PORT}/api/save-state" >/dev/null 2>&1; then
    log "Active instance saved state to disk"
else
    log "⚠ Could not save active state (may not be running)"
fi

# ── 2b. Kill legacy non-template service if it exists ─────────
#  Prevents port conflict: old philforge.service may hold port 8000
if sudo systemctl is-active "${APP}.service" >/dev/null 2>&1; then
    log "⚠ Found legacy ${APP}.service — stopping & disabling..."
    sudo systemctl stop "${APP}.service"
    sudo systemctl disable "${APP}.service" 2>/dev/null || true
fi

# ── 2c. Kill any stale process holding the standby port ───────
if sudo fuser "${STANDBY_PORT}/tcp" >/dev/null 2>&1; then
    log "⚠ Stale process on port $STANDBY_PORT — killing..."
    sudo fuser -k "${STANDBY_PORT}/tcp" 2>/dev/null || true
    sleep 1
fi
sleep 1

# ── 2d. Pre-flight smoke test (catches import errors before systemd) ──
log "Running pre-flight import check..."
if ! "$VENV/bin/python" -c "
import sys, os
os.chdir('$APP_DIR')
sys.path.insert(0, '$APP_DIR')
from app import app
print('Pre-flight OK: app imported successfully')
" 2>&1; then
    die "Pre-flight import failed! Fix the error above before deploying."
fi

# ── 3. Start standby instance ────────────────────────────────
log "Starting standby on port $STANDBY_PORT..."
sudo systemctl start "${APP}@${STANDBY_PORT}"

# Give Dhan token generation a moment (2-min rate limit per generation)
sleep 5

# ── 4. Health check standby ──────────────────────────────────
log "Waiting for standby health check..."
if ! health_check "$STANDBY_PORT"; then
    log "ROLLBACK — standby failed health check! Stopping standby."
    log "── Last 50 lines of journal for ${APP}@${STANDBY_PORT} ──"
    sudo journalctl -u "${APP}@${STANDBY_PORT}" --no-pager -n 50 --since "5 min ago" 2>&1 || true
    log "── systemctl status ──"
    sudo systemctl status "${APP}@${STANDBY_PORT}" --no-pager -l 2>&1 || true
    sudo systemctl stop "${APP}@${STANDBY_PORT}" 2>/dev/null || true
    die "Deploy aborted. Active instance on port $ACTIVE_PORT unchanged."
fi
log "Standby is healthy!"

# ── 5. Validate optional site config without moving public traffic ──
# Keep Nginx on the old worker until the new one owns the engine state. This
# intentionally creates a short cutover gap instead of a duplicate-order risk.
if [[ -f "$APP_DIR/deploy/nginx.conf" ]]; then
    if [[ "$SYNC_SITE_CONFIG" == "1" ]]; then
        sudo cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/conf.d/${APP}.conf
        log "Synced nginx site config from deploy/nginx.conf"
    else
        log "Preserving existing nginx site config (set SYNC_SITE_CONFIG=1 to overwrite)"
    fi
fi

if ! sudo nginx -t 2>/dev/null; then
    die "Nginx config test failed. Active instance on port $ACTIVE_PORT unchanged."
fi

# ── 6. Stop old worker before ownership handover ──────────────
log "Stopping old instance on port $ACTIVE_PORT..."
sudo systemctl stop "${APP}@${ACTIVE_PORT}"

# ── 7. Hand engine-state ownership to the standby ────────────
echo "$STANDBY_PORT" > "$PORT_FILE"
log "Active port state updated → $STANDBY_PORT"

# The standby started while the old port was active, so it deliberately left
# all engines passive. Restore only after the old worker has stopped; this
# removes the duplicate-order window during blue/green cutover.
# The restore fetches the scrip master, warms candle buffers and starts every
# engine, so 30s was never a realistic budget: on 2026-09-08 the call timed out
# at 30s while the restore itself finished 7 seconds later, and the deploy
# reported an engine failure that had not happened. Retrying is safe -- restore
# skips any run_id already running -- so ask twice, generously, before
# believing it.
log "Activating engine restore on port $STANDBY_PORT..."
RESTORE_OK=0
for attempt in 1 2; do
    if curl -sf --max-time 120 -X POST "http://127.0.0.1:${STANDBY_PORT}/api/restore-engines" >/dev/null; then
        RESTORE_OK=1
        break
    fi
    log "Engine restore did not answer on attempt ${attempt}; waiting before re-checking..."
    sleep 5
done

# ── 8. Move public traffic to the worker that is actually there ──
# THE OLD WORKER IS ALREADY STOPPED by this point, so the cutover has passed
# the point of no return: the standby is the only live process on the box.
# Dying here without moving nginx is what took philforge.in down on 2026-09-08
# -- traffic left pointing at a port with nothing behind it, 502 for every
# request, while the new worker sat healthy on 8001 running all five engines.
# Whatever happened to the restore, the traffic goes where the process is; a
# failure is then reported loudly, over a site that is up.
log "Switching nginx to port $STANDBY_PORT..."
echo "upstream ${APP}_backend { server 127.0.0.1:${STANDBY_PORT}; }" \
    | sudo tee "$UPSTREAM_CONF" >/dev/null
if ! sudo nginx -t 2>/dev/null; then
    die "New worker is live on port ${STANDBY_PORT}, but Nginx rejected the upstream. Repair Nginx: the site is DOWN until the upstream points there."
fi
sudo nginx -s reload
log "Nginx reloaded. New traffic → port $STANDBY_PORT"

if [[ "$RESTORE_OK" != "1" ]]; then
    die "Engines could not be confirmed on port ${STANDBY_PORT}. Traffic HAS been moved there (it is the only worker), so the site is up -- but check the broker positions and the engine panel before trusting the run."
fi

# ── 9. Point systemd's boot-start at the port that is now live ──
# Neither templated unit was enabled, so the reboot on 2026-08-10 brought the
# box back with CryptoForge running and PhilForge dead: nginx answered, the
# upstream did not, and the site served 502 indefinitely with no process to
# restart. Enabling one port once is not the fix either — the next deploy flips
# to the other and boot would start the wrong worker, which is worse, because
# systemd would then hold the port nginx is not pointing at. Enabling here, in
# step with the flip, is what keeps the two in agreement.
if sudo systemctl enable "${APP}@${STANDBY_PORT}" >/dev/null 2>&1; then
    sudo systemctl disable "${APP}@${ACTIVE_PORT}" >/dev/null 2>&1 || true
    log "Boot-start now points at port $STANDBY_PORT"
else
    # Not fatal: traffic is already served. It only means an unattended reboot
    # would come back without PhilForge, so it must be loud rather than silent.
    log "⚠ Could not enable ${APP}@${STANDBY_PORT} for boot — a reboot will NOT bring PhilForge back until this is fixed"
fi

log "═══════════════════════════════════════════════"
log "  DEPLOY COMPLETE — $APP active on port $STANDBY_PORT"
log "═══════════════════════════════════════════════"
