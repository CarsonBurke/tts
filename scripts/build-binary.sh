#!/usr/bin/env bash
set -euo pipefail

echo "scripts/build-binary.sh is deprecated; building uv launchers instead." >&2
exec "$(dirname "$0")/build-launchers.sh"
