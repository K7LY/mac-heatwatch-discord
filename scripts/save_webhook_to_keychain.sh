#!/usr/bin/env bash
set -euo pipefail

service="${1:-DISCORD_WARNING_WEBHOOK_URL}"

if [[ -z "${DISCORD_WARNING_WEBHOOK_URL:-}" ]]; then
  echo "Set DISCORD_WARNING_WEBHOOK_URL first, then rerun this script." >&2
  exit 64
fi

security add-generic-password \
  -a "$USER" \
  -s "$service" \
  -w "$DISCORD_WARNING_WEBHOOK_URL" \
  -U

echo "Saved Discord warning webhook URL to Keychain service: $service"
