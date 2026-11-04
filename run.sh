#!/usr/bin/env bash
# run.sh — Bootstrap the venv (if needed) and launch the Streamlit UI
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/.venv"
PYTHON="/usr/local/bin/python3.13"

# ── 1. Create venv if missing ───────────────────────────────────────────────
if [ ! -f "$VENV/bin/python" ]; then
  echo "[run.sh] Creating virtual environment…"
  "$PYTHON" -m venv "$VENV"
fi

# ── 2. Install / upgrade dependencies ───────────────────────────────────────
echo "[run.sh] Installing dependencies…"
"$VENV/bin/pip" install --quiet --upgrade -r requirements.txt

# ── 3. Launch Streamlit UI ───────────────────────────────────────────────────
echo "[run.sh] Starting Streamlit UI at http://localhost:8501"
"$VENV/bin/streamlit" run ui.py
