#!/usr/bin/env bash
set -euo pipefail

label="com.k7ly.mac-heatwatch-discord"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_template="$project_dir/launchd/$label.plist.in"
target_plist="$HOME/Library/LaunchAgents/$label.plist"
config_path="$HOME/Library/Application Support/mac-heat-watch/config.json"

interval_seconds="$(CONFIG_PATH="$config_path" /usr/bin/python3 - <<'PY'
import json
import os
from pathlib import Path

default = 1800
path = Path(os.environ["CONFIG_PATH"])
try:
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8")).get("interval_seconds", default)
    else:
        value = default
    interval = int(value)
except (OSError, json.JSONDecodeError, TypeError, ValueError):
    interval = default

print(max(60, interval))
PY
)"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/mac-heat-watch" "$HOME/Library/Application Support/mac-heat-watch"
sed \
  -e "s#__PROJECT_DIR__#$project_dir#g" \
  -e "s#__HOME__#$HOME#g" \
  -e "s#__START_INTERVAL__#$interval_seconds#g" \
  "$source_template" > "$target_plist"

launchctl bootout "gui/$(id -u)" "$target_plist" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$target_plist"
launchctl enable "gui/$(id -u)/$label"
launchctl kickstart -k "gui/$(id -u)/$label"

echo "Installed and started $label"
echo "Interval: $interval_seconds seconds"
echo "Logs: $HOME/Library/Logs/mac-heat-watch/"
