#!/bin/sh
# deploy.sh — sync doit source files into the Bastille jail filesystem.
#
# Usage: ./deploy.sh [--dry-run]
#
# Two variables to adjust if the jail is moved:
JAIL_ROOT="/usr/local/bastille/jails/doit/root"
APP_DIR="/var/www/doit"

DEST="${JAIL_ROOT}${APP_DIR}"

set -e

DRY_RUN=""
if [ "${1}" = "--dry-run" ]; then
    DRY_RUN="--dry-run"
    echo "==> Dry run — no files will be written"
fi

if [ ! -d "${DEST}" ]; then
    echo "==> Creating ${DEST}"
    mkdir -p "${DEST}"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Syncing source files to ${DEST}"

# src/ — Python application
rsync -av --delete $DRY_RUN \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    "${SCRIPT_DIR}/src/" "${DEST}/src/"

# requirements.txt — for pip install inside the jail
rsync -av $DRY_RUN \
    "${SCRIPT_DIR}/requirements.txt" "${DEST}/requirements.txt"

# config.json — only copy if one doesn't already exist in the jail
# (avoids overwriting production config with the example on re-deploy)
if [ -n "${DRY_RUN}" ]; then
    echo "    [dry-run] would check for existing config.json"
elif [ ! -f "${DEST}/config.json" ]; then
    echo "==> No config.json in jail — copying config.json.example as starting point"
    cp "${SCRIPT_DIR}/config.json.example" "${DEST}/config.json"
    echo "    NOTE: edit ${DEST}/config.json before starting the server"
else
    echo "==> config.json already exists in jail — not overwriting"
fi

echo ""
echo "==> Done. Files in ${DEST}:"
ls "${DEST}"
echo ""
echo "If this is a first deploy, remember to:"
echo "  1. Edit ${DEST}/config.json"
echo "  2. Install dependencies inside the jail:"
echo "       bastille cmd doit pip install -r ${APP_DIR}/requirements.txt"
echo "  3. Start the server:"
echo "       bastille cmd doit python3 ${APP_DIR}/src/serve.py"
