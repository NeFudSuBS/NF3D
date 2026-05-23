@echo off
setlocal EnableDelayedExpansion
REM ─────────────────────────────────────────────────────────────────────────────
REM  build_installer.bat  —  NF3D full build: PyInstaller → Inno Setup
REM
REM  Prerequisites:
REM    pip install pyinstaller
REM    Inno Setup 6  →  https://jrsoftware.org/isinfo.php
REM
REM  Run this script from any directory — it self-locates.
REM  Output: ..\installer_output\NF3D_Setup_1.4.exe
REM ─────────────────────────────────────────────────────────────────────────────

set "INSTALLER_DIR=%~dp0"
pushd "%INSTALLER_DIR%.."
set "APP_DIR=%CD%"

echo.
echo ============================================================
echo   NF3D  —  Build and Package
echo ============================================================
echo   App dir : %APP_DIR%
echo   Out dir : %APP_DIR%\installer_output
echo.

REM ── Step 1: Locate PyInstaller ──────────────────────────────────────────────
set "PYINSTALLER="
for %%P in (
    "%APPDATA%\Python\Python311\Scripts\pyinstaller.exe"
    "%APPDATA%\Python\Python312\Scripts\pyinstaller.exe"
    "%APPDATA%\Python\Python310\Scripts\pyinstaller.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\Scripts\pyinstaller.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\Scripts\pyinstaller.exe"
) do (
    if exist %%P (
        if not defined PYINSTALLER set "PYINSTALLER=%%~P"
    )
)

REM Fallback: try PATH
if not defined PYINSTALLER (
    where pyinstaller >nul 2>&1
    if !ERRORLEVEL! equ 0 set "PYINSTALLER=pyinstaller"
)

if not defined PYINSTALLER (
    echo *** ERROR: pyinstaller not found.
    echo     Run:  pip install pyinstaller
    popd & pause & exit /b 1
)

REM ── Check NF3D is not running ────────────────────────────────────────────────
tasklist /FI "IMAGENAME eq NF3D.exe" 2>nul | find /I "NF3D.exe" >nul
if !ERRORLEVEL! equ 0 (
    echo *** ERROR: NF3D.exe is currently running.
    echo     Close the application and try again.
    popd & pause & exit /b 1
)

echo [1/2]  Running PyInstaller...
echo        Using: %PYINSTALLER%
echo.

"%PYINSTALLER%" --noconfirm "installer\NF3D.spec"
if %ERRORLEVEL% neq 0 (
    echo.
    echo *** ERROR: PyInstaller failed — see output above.
    popd & pause & exit /b 1
)

echo.
echo        PyInstaller done.  Output: %APP_DIR%\dist\NF3D\
echo.

REM ── Step 2: Inno Setup ──────────────────────────────────────────────────────
echo [2/2]  Running Inno Setup compiler...
echo.

set "ISCC="
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
    "%ProgramFiles%\Inno Setup 5\ISCC.exe"
) do (
    if exist %%P (
        if not defined ISCC set "ISCC=%%~P"
    )
)

if not defined ISCC (
    echo *** ERROR: Inno Setup compiler (ISCC.exe) not found.
    echo     Install Inno Setup 6 from  https://jrsoftware.org/isinfo.php
    popd & pause & exit /b 1
)

echo        Using: %ISCC%
echo.

"%ISCC%" "installer\NF3D_setup.iss"
if %ERRORLEVEL% neq 0 (
    echo.
    echo *** ERROR: Inno Setup compilation failed — see output above.
    popd & pause & exit /b 1
)

echo.
echo ============================================================
echo   Done!
echo   Installer: %APP_DIR%\installer_output\NF3D_Setup_1.4.exe
echo ============================================================
echo.

popd
pause
