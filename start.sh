#!/usr/bin/env bash
# Start the Dagwood API and UI together. Ctrl-C stops both.
#
#   ./start.sh
#
# Expects the one-time setup from the README: a .venv with requirements installed and a .env
# holding DEEPINFRA_API_KEY. UI packages are installed on first run.
# Written for the bash 3.2 that ships with macOS as well as Linux and WSL.

set -u
cd "$(dirname "$0")"

fail() {
    echo "start.sh: $1" >&2
    exit 1
}

[ -x .venv/bin/python ] || fail "no .venv found. Run:
    python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt"
.venv/bin/python -c "import uvicorn" 2>/dev/null || fail "the .venv is missing packages. Run:
    .venv/bin/pip install -r requirements.txt"
[ -f .env ] || fail "no .env found. Run:
    cp .env.example .env
then set DEEPINFRA_API_KEY in it."
grep -Eq '^[[:space:]]*DEEPINFRA_API_KEY=.+' .env || echo "start.sh: DEEPINFRA_API_KEY is empty in .env; the UI will load but runs will fail." >&2
command -v npm >/dev/null 2>&1 || fail "npm not found. Install Node 20.19+ or 22.12+."

if [ ! -d ui/node_modules ]; then
    echo "start.sh: installing UI packages (first run only)..."
    (cd ui && npm install) || fail "npm install failed."
fi

port_open() {
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

for port in 8000 5173; do
    if port_open "$port"; then
        fail "port $port is already in use. Is Dagwood already running in another terminal?"
    fi
done

API_PID=""
UI_PID=""

stop_tree() {  # a process and everything under it (uvicorn's reload worker; npm -> sh -> vite)
    [ -n "$1" ] || return 0
    local child
    for child in $(pgrep -P "$1" 2>/dev/null); do
        stop_tree "$child"
    done
    kill -TERM "$1" 2>/dev/null
}

cleanup() {
    trap - INT TERM EXIT
    echo
    echo "start.sh: stopping..."
    stop_tree "$UI_PID"
    stop_tree "$API_PID"
    wait 2>/dev/null
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

.venv/bin/python -m uvicorn orchestrator.api:app --reload --port 8000 &
API_PID=$!

# The API takes a while to import; starting the UI first fills the terminal with proxy errors
# from any browser tab that is already open.
echo "start.sh: starting the API on http://localhost:8000. This can take up to a minute."
echo "start.sh: the UI starts once the API is ready; its address will appear below."
waited=0
until port_open 8000; do
    kill -0 "$API_PID" 2>/dev/null || fail "the API stopped while starting; see its output above."
    [ "$waited" -lt 120 ] || fail "the API did not answer within 2 minutes; see its output above."
    sleep 1
    waited=$((waited + 1))
done

(cd ui && exec npm run dev) &
UI_PID=$!

echo "start.sh: the API is ready. Starting the UI on http://localhost:5173 (Ctrl-C stops both)"

# Leave as soon as either one exits, so a failed start (a port already in use, say) doesn't
# leave the other half running on its own.
while kill -0 "$API_PID" 2>/dev/null && kill -0 "$UI_PID" 2>/dev/null; do
    sleep 1
done

if kill -0 "$API_PID" 2>/dev/null; then
    echo "start.sh: the UI stopped; see its output above." >&2
else
    echo "start.sh: the API stopped; see its output above." >&2
fi
exit 1
