@echo off
setlocal enabledelayedexpansion

:: WebUI LLM Proxy - Stop Script

set "PROXY_PORT=8080"
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
cd /d "%PROJECT_DIR%"

set "PID_FOUND=0"
set "PID_TO_KILL="

:: Method 1: Stop by PID file (preferred)
if exist "data\server.pid" (
    set /p PID_FROM_FILE=<"data\server.pid"
    if not "!PID_FROM_FILE!"=="" (
        tasklist /FI "PID eq !PID_FROM_FILE!" 2>nul | findstr "!PID_FROM_FILE!" >nul
        if !errorlevel! equ 0 (
            echo [INFO] Stopping service via PID file ^(PID: !PID_FROM_FILE!^)
            taskkill /PID !PID_FROM_FILE! /F >nul 2>&1
            set "PID_FOUND=1"
            set "PID_TO_KILL=!PID_FROM_FILE!"
        )
    )
)

:: Method 2: Stop by port scan (fallback)
if !PID_FOUND! equ 0 (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PROXY_PORT%" ^| findstr "LISTENING"') do (
        if not "%%a"=="0" (
            if !PID_FOUND! equ 0 (
                echo [INFO] Stopping service via port scan ^(PID: %%a, Port: %PROXY_PORT%^)
                taskkill /PID %%a /F >nul 2>&1
                set "PID_FOUND=1"
                set "PID_TO_KILL=%%a"
            )
        )
    )
)

:: Cleanup PID file
if exist "data\server.pid" (
    del /f /q "data\server.pid" >nul 2>&1
)

if !PID_FOUND! equ 1 (
    echo [OK]   Service stopped ^(PID: !PID_TO_KILL!^)
) else (
    echo [INFO] No running service found on port %PROXY_PORT%
)

endlocal
