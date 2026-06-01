#!/usr/bin/env bash
set -euo pipefail

label="com.k7ly.mac-heatwatch-discord"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_template="$project_dir/launchd/$label.plist.in"
target_plist="$HOME/Library/LaunchAgents/$label.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/mac-heat-watch" "$HOME/Library/Application Support/mac-heat-watch"
sed \
  -e "s#__PROJECT_DIR__#$project_dir#g" \
  -e "s#__HOME__#$HOME#g" \
  "$source_template" > "$target_plist"

launchctl bootout "gui/$(id -u)" "$target_plist" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$target_plist"
launchctl enable "gui/$(id -u)/$label"
launchctl kickstart -k "gui/$(id -u)/$label"

echo "Installed and started $label"
echo "Logs: $HOME/Library/Logs/mac-heat-watch/"
