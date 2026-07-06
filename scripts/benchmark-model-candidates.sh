#!/usr/bin/env bash
set -euo pipefail

RUNS="${RUNS:-2}"
TEXT="${TEXT:-Build finished. One migration test is failing and needs review.}"
REFERENCE_AUDIO="${REFERENCE_AUDIO:-}"
REFERENCE_TEXT="${REFERENCE_TEXT:-}"

run_benchmark() {
  local backend="$1"
  local env_dir=".venv-${backend}"
  shift

  echo "== $backend =="
  if [[ ! -x "$env_dir/bin/tts" ]]; then
    echo "Missing $env_dir/bin/tts; run scripts/install-model-candidate-envs.sh first."
    echo
    return 0
  fi

  echo "Python: $("$env_dir/bin/python" --version 2>&1)"
  "$env_dir/bin/tts" benchmark --backend "$backend" --runs "$RUNS" "$@" "$TEXT" || true
  echo
}

run_benchmark qwen3 --speaker Aiden --language English
run_benchmark chatterbox
run_benchmark kokoro --language a --speaker af_heart
run_benchmark vibevoice --model-size 0.5
run_benchmark vibevoice --model-size 1.5

if [[ -n "$REFERENCE_AUDIO" && -n "$REFERENCE_TEXT" ]]; then
  run_benchmark neutts --reference-audio "$REFERENCE_AUDIO" --reference-text "$REFERENCE_TEXT"
  run_benchmark omnivoice --reference-audio "$REFERENCE_AUDIO" --reference-text "$REFERENCE_TEXT"
else
  echo "Skipping NeuTTS and OmniVoice: set REFERENCE_AUDIO and REFERENCE_TEXT to test reference-based models."
fi
