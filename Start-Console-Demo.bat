@echo off
rem Double-click to try the console without any NI hardware or driver: synthetic motor signals.
rem Demo recordings go to demo_data\ so they never mix with real runs in data\.
call "%~dp0Start-Console.bat" --demo --data demo_data %*
