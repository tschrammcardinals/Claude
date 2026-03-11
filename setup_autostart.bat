@echo off
REM ============================================================
REM  Kalshi Tennis Notifier - Windows Auto-Start Setup
REM ============================================================
REM  This script creates a scheduled task that launches the
REM  notifier automatically when you log in to Windows.
REM
REM  Run this script once (as Administrator) to set up auto-start.
REM  To remove: run   schtasks /delete /tn "KalshiTennisNotifier" /f
REM ============================================================

setlocal

REM --- Find Python ---
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found in PATH. Please install Python 3.10+ first.
    echo Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM --- Get the directory this script lives in ---
set "SCRIPT_DIR=%~dp0"
set "NOTIFIER=%SCRIPT_DIR%kalshi_tennis_notifier.py"

if not exist "%NOTIFIER%" (
    echo ERROR: kalshi_tennis_notifier.py not found in %SCRIPT_DIR%
    pause
    exit /b 1
)

REM --- Create a VBS wrapper to run without a visible console window ---
set "VBS_PATH=%SCRIPT_DIR%kalshi_tennis_notifier_hidden.vbs"
echo Creating silent launcher: %VBS_PATH%

(
echo Set WshShell = CreateObject^("WScript.Shell"^)
echo WshShell.Run "python ""%NOTIFIER%"" --sound", 0, False
) > "%VBS_PATH%"

REM --- Create the scheduled task ---
echo.
echo Creating scheduled task "KalshiTennisNotifier"...
schtasks /create /tn "KalshiTennisNotifier" /tr "wscript.exe \"%VBS_PATH%\"" /sc onlogon /rl highest /f

if %errorlevel% equ 0 (
    echo.
    echo SUCCESS! The Kalshi Tennis Notifier will now start automatically
    echo when you log in to Windows.
    echo.
    echo To start it right now, run:
    echo   python "%NOTIFIER%"
    echo.
    echo To remove auto-start later, run:
    echo   schtasks /delete /tn "KalshiTennisNotifier" /f
) else (
    echo.
    echo FAILED to create scheduled task. Try running this script as Administrator:
    echo   Right-click setup_autostart.bat ^> Run as administrator
)

echo.
pause
