#!/usr/bin/env bash
# Compatibility entry point. All web settings, health checks and units live in install.sh.
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$HERE/install.sh" --panel
