#!/usr/bin/env bash
set -euo pipefail

VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
  echo "Created virtual environment in $VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
echo "Virtual environment activated. Python: $(python3 --version)"

pip install --upgrade pip >/dev/null
pip install \
  requests \
  playwright \
  google-auth \
  google-auth-oauthlib \
  google-api-python-client \
  openai >/dev/null

echo "Base dependencies installed. If browsers are missing, run: python -m playwright install"
echo "To use the venv in this shell, run: source $VENV_DIR/bin/activate"
