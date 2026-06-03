@echo off
setlocal enabledelayedexpansion

:: WebUI LLM Proxy - Status Script

set "PROXY_PORT=8080"
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
cd /d "%PROJECT_DIR%"

set "SERVICE_RUNNING=0"
set "SERVICE_PID="
set "SERVICE_NAME="

:: Method 1: Check PID file
if exist "data\server.pid" (
    set /p PID_FROM_FILE=<"data\server.pid"
    if not "!PID_FROM_FILE!"=="" (
        for /f "skip=3 tokens=2,*" %%a in ('tasklist /FI "PID eq !PID_FROM_FILE!" 2^>nul') do (
            if "%%a"=="!PID_FROM_FILE!" (
                set "SERVICE_RUNNING=1"
                set "SERVICE_PID=!PID_FROM_FILE!"
                set "SERVICE_NAME=%%b"
            )
        )
    )
)

:: Method 2: Check port (fallback)
if !SERVICE_RUNNING! equ 0 (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PROXY_PORT%" ^| findstr "LISTENING"') do (
        if not "%%a"=="0" (
            set "SERVICE_RUNNING=1"
            set "SERVICE_PID=%%a"
        )
    )
)

if !SERVICE_RUNNING! equ 1 (
    echo ============================================
    echo  WebUI LLM Proxy - Status: RUNNING
    echo ============================================
    echo  PID     : !SERVICE_PID!
    if not "!SERVICE_NAME!"=="" (
        echo  Process : !SERVICE_NAME!
    )
    echo  Port    : %PROXY_PORT%
    echo  URL     : http://localhost:%PROXY_PORT%
    echo  API     : http://localhost:%PROXY_PORT%/v1/chat/completions
    echo  Docs    : http://localhost:%PROXY_PORT%/docs
    echo  Log     : data\logs\server.log
    echo --------------------------------------------
    
    :: Show recent log lines
    if exist "data\logs\server.log" (
        echo.
        echo  Last 5 log lines:
        for /f "skip=1 delims=" %%l in ('powershell -Command "Get-Content 'data\logs\server.log' -Tail 5"') do (
            echo    %%l
        )
    )
) else (
    echo ============================================
    echo  WebUI LLM Proxy - Status: STOPPED
    echo ============================================
    echo  No service running on port %PROXY_PORT%
    echo --------------------------------------------
    echo  Start with: start.bat
)

endlocal
