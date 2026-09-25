@echo off
REM G-MES report workflow launcher.
REM   Double-click                        -> interactive prompts, pauses at the end.
REM   GMES_Workflow.bat P1112UM00 ...     -> passed straight to gmes_report.py run
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: python was not found on PATH.
    echo Install Python 3, or run the scripts with a full path to python.exe.
    pause
    exit /b 1
)

REM Read-only: Python version, websocket-client, a supported browser, and a
REM writable runtime directory - before the first real sign-in spends any of
REM the account's patience on a problem this could have caught instantly.
python gmes_preflight.py
if errorlevel 1 (
    pause
    exit /b 1
)

if "%~1"=="" (
    python run_gmes_workflow.py
    goto :eof
)

python gmes_report.py run %*
exit /b %ERRORLEVEL%
