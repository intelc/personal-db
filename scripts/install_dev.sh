#!/usr/bin/env bash
set -euo pipefail
uv venv
uv pip install -e ".[dev]"
echo "Activate: source .venv/bin/activate"
