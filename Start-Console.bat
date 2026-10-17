@echo off
rem Double-click to start the sampling console (real NI hardware).
rem First run: creates .venv and installs the Python packages (needs internet, a few minutes).
rem Extra options pass through, e.g.  Start-Console.bat --stopped   or   --port 8060
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up Python ^(.venv^) ...
  powershell -NoProfile -ExecutionPolicy Bypass -File setup\windows_setup.ps1
  if errorlevel 1 ( echo. & echo Setup failed, see the messages above. & pause & exit /b 1 )
)
".venv\Scripts\python.exe" daq\console\server.py %*
if errorlevel 1 pause
