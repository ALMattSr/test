#!/bin/bash
# Double-click this file on a Mac to open TV Renamer in your web browser.
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 isn't installed. Install it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Enter to close."
  exit 1
fi
python3 tvrename_gui.py
