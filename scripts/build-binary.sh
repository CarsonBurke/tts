#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv-build}"
NAME="${NAME:-stt}"
SPACY_MODEL_URL="${SPACY_MODEL_URL:-https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl}"

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install -U pip "setuptools<82" wheel
"$VENV_DIR/bin/python" -m pip install -e ".[kokoro]"
"$VENV_DIR/bin/python" -m pip install "$SPACY_MODEL_URL"
"$VENV_DIR/bin/python" -m pip install pyinstaller

"$VENV_DIR/bin/pyinstaller" \
  --clean \
  --onefile \
  --name "$NAME" \
  --collect-all kokoro \
  --collect-all misaki \
  --collect-data language_tags \
  --collect-data csvw \
  --collect-data segments \
  --collect-data espeakng_loader \
  --collect-all en_core_web_sm \
  --collect-data spacy \
  --collect-data thinc \
  --collect-submodules spacy.lang.en \
  --hidden-import stt_cli.backends.kokoro \
  --hidden-import stt_cli.backends.system \
  scripts/stt_launcher.py
