#!/bin/sh
set -e
# Ensure Chromium path is consistent across services.
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/ms-playwright}"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"

NORMALIZER_MODE="generic_default"
if [ -n "${NORMALIZER_DOMAIN_TERMS:-}" ] || [ "${NORMALIZER_QUERY_EXPANSION:-false}" = "true" ] || [ "${NORMALIZER_SLOTS_ENABLED:-false}" = "true" ]; then
  NORMALIZER_MODE="legacy_compat"
fi
echo "Hybrid normalizer mode: $NORMALIZER_MODE"

if [ "${PLAYWRIGHT_INSTALL_ON_STARTUP:-false}" = "true" ]; then
  echo "Installing Playwright Chromium at startup in $PLAYWRIGHT_BROWSERS_PATH ..."
  python -m playwright install chromium
else
  echo "Skipping Playwright install at startup (PLAYWRIGHT_INSTALL_ON_STARTUP=false)"
fi
exec "$@"
