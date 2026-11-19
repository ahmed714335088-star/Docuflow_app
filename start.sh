#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.11+ is required. Install Python and run start.sh again."
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "FFmpeg and ffprobe are required. Ubuntu/Debian: sudo apt-get install ffmpeg libass9 fonts-noto-core"
  exit 1
fi
if ! ffmpeg -hide_banner -filters 2>/dev/null | grep -q ' subtitles '; then
  echo "This FFmpeg build needs the subtitles/libass filter. Install a full FFmpeg build with libass support."
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
export HOST="127.0.0.1"
export PORT="${PORT:-3000}"
echo "DocuFlow is ready at http://127.0.0.1:${PORT}"
exec .venv/bin/python -m app.main
