@echo off
setlocal enabledelayedexpansion

:: WebUI LLM Proxy - Start Script
:: Usage: start.bat [--keep-chat]

set "PROXY_PORT=8080"
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
cd /d "%PROJECT_DIR%"

:: Parse arguments
set "KEEP_CHAT_FLAG="
set "MODELS_FLAG="
:parse_args
if /I "%~1"=="--help" goto :show_help
if /I "%~1"=="/help" goto :show_help
if /I "%~1"=="-h" goto :show_help
if /I "%~1"=="--keep-chat" set "KEEP_CHAT_FLAG=1"
if /I "%~1"=="--models" (
    set "MODELS_FLAG=%~2"
    shift
    shift
    goto :collect_models
)
shift
if not "%~1"=="" goto :parse_args

:: Windows cmd treats comma as arg separator; collect following non-flag args
:collect_models
if not "%~1"=="" (
    set "ARG=%~1"
    set "FIRST_CHAR=!ARG:~0,1!"
    if not "!FIRST_CHAR!"=="-" (
        set "MODELS_FLAG=!MODELS_FLAG!,!ARG!"
        shift
        goto :collect_models
    )
)
goto :parse_args_done
:parse_args_done

:: Ensure directories exist
if not exist "data\logs" mkdir "data\logs"
if not exist "data\media" mkdir "data\media"
if not exist "data\uploads" mkdir "data\uploads"

:: Check if already running (via PID file)
if exist "data\server.pid" (
    set /p EXISTING_PID=<"data\server.pid"
    if not "!EXISTING_PID!"=="" (
        tasklist /FI "PID eq !EXISTING_PID!" 2>nul | findstr "!EXISTING_PID!" >nul
        if !errorlevel! equ 0 (
            echo [ERROR] Service already running on port %PROXY_PORT% ^(PID: !EXISTING_PID!^)
            echo          Use: status.bat  ^(check status^)
            echo          Use: restart.bat ^(restart service^)
            goto :end
        )
    )
)

:: Check if port is occupied by another process
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PROXY_PORT%" ^| findstr "LISTENING"') do (
    if not "%%a"=="0" (
        echo [WARNING] Port %PROXY_PORT% is occupied by PID %%a
        echo           Service may conflict. Use: stop.bat
    )
)

echo ============================================
echo  WebUI LLM Proxy - Starting...
echo ============================================
if defined KEEP_CHAT_FLAG (
    echo  Mode    : keep-chat ^(sessions preserved^)
) else (
    echo  Mode    : auto-delete ^(default^)
)
if defined MODELS_FLAG (
    echo  Models  : %MODELS_FLAG%
) else (
    echo  Models  : gemini,kimi ^(default^)
)
echo  Port    : %PROXY_PORT%
echo  Log     : data\logs\server.log
echo  PID     : data\server.pid
echo --------------------------------------------

:: Capture existing python PIDs before starting
set "EXISTING_PIDS="
for /f "skip=3 tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" 2^>nul') do (
    set "EXISTING_PIDS=!EXISTING_PIDS! %%a"
)

:: Build command line
set "CMD_ARGS=--port %PROXY_PORT%"
if defined KEEP_CHAT_FLAG set "CMD_ARGS=%CMD_ARGS% --keep-chat"

:: Start service in background
:: NOTE: We use a helper .bat because setlocal-scoped vars are NOT inherited
::       by start /b child processes. The helper sets the env var locally,
::       then start /b's python inherits it.
if defined MODELS_FLAG (
    > "data\_start_helper.bat" echo @echo off
    >> "data\_start_helper.bat" echo set "PROXY_ENABLED_MODELS=%MODELS_FLAG%"
    >> "data\_start_helper.bat" echo start /b "" python -m webui_llm_proxy %CMD_ARGS% ^> "data\logs\server.log" 2^>^&1
    call "data\_start_helper.bat"
    del "data\_start_helper.bat" 2>nul
) else (
    start /b "" python -m webui_llm_proxy %CMD_ARGS% > "data\logs\server.log" 2>&1
)

:: Wait briefly for process to appear
timeout /t 2 /nobreak >nul 2>&1

:: Find the new python PID by comparing with existing
set "SERVICE_PID="
for /f "skip=3 tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" 2^>nul') do (
    echo !EXISTING_PIDS! | findstr /C:" %%a " >nul
    if !errorlevel! neq 0 (
        if not defined SERVICE_PID (
            set "SERVICE_PID=%%a"
        )
    )
)

:: Fallback: find PID by port
if not defined SERVICE_PID (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PROXY_PORT%" ^| findstr "LISTENING"') do (
        if not "%%a"=="0" (
            set "SERVICE_PID=%%a"
        )
    )
)

:: Save PID to file
if defined SERVICE_PID (
    echo !SERVICE_PID! > "data\server.pid"
    echo [INFO] Service PID: !SERVICE_PID!
) else (
    echo [WARN] Could not detect service PID
)

:: Wait for startup
timeout /t 2 /nobreak >nul 2>&1

:: Verify startup by checking log
call :check_startup

goto :end

:show_help
echo ============================================
echo  WebUI LLM Proxy - Start Script
echo ============================================
echo.
echo  Usage:
echo    start.bat                      Start service ^(default mode^)
echo    start.bat --keep-chat          Start service ^(keep sessions^)
echo    start.bat --models gemini      Start with Gemini only
echo    start.bat --models kimi        Start with Kimi only
echo    start.bat --models gemini,kimi Start with both ^(default^)
echo    start.bat /help                Show this help
echo.
echo  Related:
echo    stop.bat               Stop service
echo    restart.bat            Restart service
echo    status.bat             Check status
echo.
goto :end

:check_startup
if not exist "data\logs\server.log" (
    echo [WARN] Log file not found. Service may still be starting.
    goto :eof
)

:: Check log for startup indicators
findstr /C:"Application startup complete" "data\logs\server.log" >nul 2>&1
if !errorlevel! equ 0 (
    echo [OK]   Service started successfully!
    echo.
    echo        URL: http://localhost:%PROXY_PORT%
    echo        API: http://localhost:%PROXY_PORT%/v1/chat/completions
    echo        Doc: http://localhost:%PROXY_PORT%/docs
    goto :eof
)

findstr /C:"Ready" "data\logs\server.log" >nul 2>&1
if !errorlevel! equ 0 (
    echo [OK]   Service started successfully!
    echo        URL: http://localhost:%PROXY_PORT%
    goto :eof
)

:: Check for error indicators
findstr /C:"Error" /C:"ERROR" /C:"Traceback" "data\logs\server.log" >nul 2>&1
if !errorlevel! equ 0 (
    echo [WARN] Errors detected in log. Check: data\logs\server.log
    goto :eof
)

echo [INFO] Service is starting. Please wait or check: data\logs\server.log
goto :eof

:end
endlocal
