@echo off
setlocal enabledelayedexpansion

:: WebUI LLM Proxy - Restart Script
:: Usage: restart.bat [--keep-chat] [--models gemini,kimi]

set "SCRIPT_DIR=%~dp0"

echo ============================================
echo  WebUI LLM Proxy - Restarting...
echo ============================================

:: Stop
call "%SCRIPT_DIR%stop.bat"

:: Wait for port release
timeout /t 2 /nobreak >nul 2>&1

:: Start (pass through all arguments)
call "%SCRIPT_DIR%start.bat" %*

endlocal
