#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

install_env() {
  local backend="$1"
  local env_dir=".venv-${backend}"

  echo "== ${backend}: ${env_dir} =="
  "$PYTHON_BIN" -m venv "$env_dir"
  "$env_dir/bin/python" -m pip install -U pip setuptools wheel
  "$env_dir/bin/python" -m pip install -e ".[${backend}]"
  echo
}

install_env qwen3
install_env chatterbox
install_env kokoro
install_env neutts
install_env omnivoice
install_env vibevoice
