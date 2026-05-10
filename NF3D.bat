@echo off
REM NF3D Launcher
REM Double-click this file to launch NF3D.

REM -- Option A: if you created a venv at C:\NF3D\venv
REM Uncomment and set PYTHON to your venv, then comment out Option B:
REM set PYTHON=C:\NF3D\venv\Scripts\python.exe
REM goto launch

REM -- Option B: use py launcher with explicit version (default)
set PYTHON=py -3.11

:launch
set DIR=%~dp0

%PYTHON% --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python 3.11 not found.
    echo Install from https://python.org/downloads
    pause
    exit /b 1
)

REM Run setup wizard only when deps are unverified. Once all deps pass,
REM setup_check writes .setup_ok and subsequent launches go straight to the app.
REM Delete .setup_ok to force the wizard to run again.
if exist "%DIR%.setup_ok" (
    start "NF3D" /b %PYTHON% "%DIR%nf3d_gui.py"
) else (
    %PYTHON% "%DIR%setup_check.py"
)
