#!/usr/bin/env bash
# Create the project virtualenv (.venv) and install Python packages. No sudo needed.
# Needs python3-venv (installed by 01_install_ni_daqmx.sh).
#   bash setup/linux/03_setup_python.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python3 -m venv "${ROOT}/.venv"
"${ROOT}/.venv/bin/python" -m pip install --upgrade pip
"${ROOT}/.venv/bin/python" -m pip install -r "${ROOT}/requirements.txt"
"${ROOT}/.venv/bin/python" -m pip freeze > "${ROOT}/requirements.lock.txt"
echo "Python env ready: ${ROOT}/.venv  (locked versions in requirements.lock.txt)"
