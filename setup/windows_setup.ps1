# One-time Python setup on the Windows acquisition PC. Run it from the repo folder:
#   powershell -ExecutionPolicy Bypass -File setup\windows_setup.ps1
# Creates .venv, installs requirements.txt, and checks that Python can talk to NI-DAQmx.
# Install NI-DAQmx first (GUIDEBOOK.md section 3); step 3 tells you how if it is missing.
# Written on Linux and not yet run on Windows. If it fails, the manual steps are in GUIDEBOOK.md.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Run([string]$exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "failed (exit $LASTEXITCODE): $exe $($argv -join ' ')" }
}

Write-Host "=== 1/3  Python virtual environment: $Venv"
if (-not (Test-Path $Py)) {
    if (Get-Command py -ErrorAction SilentlyContinue) { Run "py" @("-3", "-m", "venv", $Venv) }
    else { throw "Python launcher 'py' not found. Install Python first:  winget install Python.Python.3.13" }
}
Run $Py @("-c", "import sys; assert sys.version_info >= (3, 11), 'need Python 3.11+, found ' + sys.version")

Write-Host "=== 2/3  Install Python packages (requirements.txt)"
Run $Py @("-m", "pip", "install", "--upgrade", "pip")
Run $Py @("-m", "pip", "install", "-r", (Join-Path $Root "requirements.txt"))

Write-Host "=== 3/3  Check the NI-DAQmx driver"
& $Py -c "import nidaqmx.system as s; v = s.System.local().driver_version; print(f'NI-DAQmx {v.major_version}.{v.minor_version}.{v.update_version} found')"
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "NI-DAQmx is not installed yet (or the PC needs a reboot after installing it)."
    Write-Host "Install it from ni.com (keep 'NI Measurement & Automation Explorer' ticked), or run:"
    Write-Host "    $Py -m nidaqmx installdriver"
    Write-Host "Reboot, then run this script again."
    exit 1
}
Write-Host ""
Write-Host "Ready. Next:  .venv\Scripts\python daq\daq_check.py devices"
