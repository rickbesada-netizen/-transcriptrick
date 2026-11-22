#!/usr/bin/env bash
# macOS and Linux launcher.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Transcriptrick. This takes a few minutes and only happens once."
  python3 -m venv .venv
fi
if ! cmp -s requirements.txt .venv/installed.txt; then
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
  cp requirements.txt .venv/installed.txt
else
  .venv/bin/python -m pip install --quiet --disable-pip-version-check --upgrade yt-dlp yt-dlp-ejs "youtube-transcript-api<2" >/dev/null 2>&1 || true
fi
exec .venv/bin/python -m app
