#!/usr/bin/env bash
# Runs amazon_photos_dl.py in a local venv (and keeps the Mac awake on macOS).
# Usage:
#   ./run.sh --tld fr --list
#   ./run.sh --tld fr --out "/Volumes/MyDisk/AmazonPhotos"
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"

if ! command -v python3 >/dev/null; then
  echo "python3 not found. Install it with: xcode-select --install"; exit 1
fi

if [ ! -d "$VENV" ]; then
  echo "Creating venv…"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q requests
fi

if [ ! -f cookies.json ]; then
  echo "cookies.json not found next to the script (see cookies.example.json)."; exit 1
fi
chmod 600 cookies.json

# On macOS, keep the Mac awake (no idle/disk/system sleep) for as long as the script runs.
if [ "$(uname -s)" = "Darwin" ] && command -v caffeinate >/dev/null; then
  echo "macOS detected: caffeinate active until the script ends."
  exec caffeinate -ims "$VENV/bin/python" amazon_photos_dl.py "$@"
else
  exec "$VENV/bin/python" amazon_photos_dl.py "$@"
fi
