#!/usr/bin/env bash
set -euo pipefail

NAME="${NAME:-tts}"
DAEMON_PACKAGE="${DAEMON_PACKAGE:-tts[kokoro] @ git+https://github.com/CarsonBurke/tts@main}"
DAEMON_PYTHON="${DAEMON_PYTHON:-3.12}"

mkdir -p dist
if command -v go >/dev/null 2>&1 && go version >/dev/null 2>&1; then
  go build \
    -ldflags "-X 'main.daemonPackage=${DAEMON_PACKAGE}' -X 'main.daemonPython=${DAEMON_PYTHON}'" \
    -o "dist/$NAME" \
    ./cmd/tts-client
elif command -v mise >/dev/null 2>&1; then
  mise exec go@1.26.1 -- go build \
    -ldflags "-X 'main.daemonPackage=${DAEMON_PACKAGE}' -X 'main.daemonPython=${DAEMON_PYTHON}'" \
    -o "dist/$NAME" \
    ./cmd/tts-client
else
  echo "Missing Go toolchain." >&2
  exit 1
fi
