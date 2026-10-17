# One-time Python setup on the Windows acquisition PC. Run it from the repo folder:
#   powershell -ExecutionPolicy Bypass -File setup\windows_setup.ps1
# Creates .venv, installs requirements.txt, and checks that Python can talk to NI-DAQmx.
# Start-Console.bat runs this automatically on first use. NI-DAQmx is only needed for real
# hardware: without it, setup still finishes and the console works in demo mode.
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
    elseif (Get-Command python -ErrorAction SilentlyContinue) { Run "python" @("-m", "venv", $Venv) }
    else { throw "Python not found. Install Python 3.11+ first:  winget install Python.Python.3.13  (then open a new window)" }
}
Run $Py @("-c", "import sys; assert sys.version_info >= (3, 11), 'need Python 3.11+, found ' + sys.version")

Write-Host "=== 2/3  Install Python packages (requirements.txt)"
Run $Py @("-m", "pip", "install", "--upgrade", "pip")
Run $Py @("-m", "pip", "install", "-r", (Join-Path $Root "requirements.txt"))

Write-Host "=== 3/3  Check the NI-DAQmx driver"
& $Py -c "import nidaqmx.system as s; v = s.System.local().driver_version; print(f'NI-DAQmx {v.major_version}.{v.minor_version}.{v.update_version} found')" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "NI-DAQmx is not installed (or the PC needs a reboot after installing it)." -ForegroundColor Yellow
    Write-Host "Demo mode works anyway (Start-Console-Demo.bat). For real hardware, install NI-DAQmx"
    Write-Host "from ni.com (keep 'NI Measurement & Automation Explorer' ticked), or run:"
    Write-Host "    $Py -m nidaqmx installdriver"
    Write-Host "then reboot."
}
Write-Host ""
Write-Host "Python setup done. Start the console:  Start-Console.bat  (or Start-Console-Demo.bat)"
