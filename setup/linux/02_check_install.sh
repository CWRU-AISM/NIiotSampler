#!/usr/bin/env bash
# Post-reboot check of the NI-DAQmx install. No sudo needed.
#   bash setup/linux/02_check_install.sh
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ok()   { echo "  [OK]   $*"; }
fail() { echo "  [FAIL] $*"; FAILED=1; }
FAILED=0

echo "Kernel: $(uname -r)    Secure Boot: $(mokutil --sb-state 2>/dev/null | head -1)"

echo "Kernel modules"
for m in nikal nipalk; do
    if lsmod | grep -q "^${m}\b"; then ok "${m} loaded"; else fail "${m} not loaded (dmesg | grep -i -E 'nipal|nikal|key|sign')"; fi
done

echo "Services"
for s in nipal; do
    st=$(systemctl is-active "${s}" 2>/dev/null)
    [[ "${st}" == "active" ]] && ok "${s}.service active" || fail "${s}.service ${st:-missing}"
done

echo "NI-DAQmx tools"
if command -v nilsdev >/dev/null; then
    ok "nilsdev found; devices currently known to DAQmx:"
    nilsdev --verbose 2>&1 | sed 's/^/         /'
else
    fail "nilsdev not found"
fi

echo "Python environment"
PY="${HERE}/../../.venv/bin/python"
if [[ -x "${PY}" ]]; then
    "${PY}" -c "
from importlib.metadata import version
import nidaqmx.system as s
v = s.System.local().driver_version
print(f'  [OK]   nidaqmx {version(\"nidaqmx\")} -> NI-DAQmx {v.major_version}.{v.minor_version}.{v.update_version}')" \
        || fail "python could not talk to NI-DAQmx"
else
    fail ".venv missing - run: bash setup/linux/03_setup_python.sh"
fi

echo
[[ ${FAILED} -eq 0 ]] && echo "All checks passed." || echo "Some checks failed (see above)."
exit ${FAILED}
