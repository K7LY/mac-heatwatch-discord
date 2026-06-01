#!/usr/bin/env bash
set -euo pipefail

label="com.k7ly.mac-heatwatch-discord"
target_plist="$HOME/Library/LaunchAgents/$label.plist"

launchctl bootout "gui/$(id -u)" "$target_plist" >/dev/null 2>&1 || true
rm -f "$target_plist"

echo "Uninstalled $label"
