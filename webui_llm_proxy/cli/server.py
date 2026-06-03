"""
服务守护进程管理 — start / stop / restart / status / logs
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "logs", "server.log")
PID_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "server.pid")


def start(keep_chat: bool = False) -> None:
    """启动服务"""
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            print(f"Service already running (PID: {old_pid})")
            return
        except (OSError, ValueError):
            pass

    print("Starting WebUI LLM Proxy...")
    if keep_chat:
        print("Mode: keep-chat")
    else:
        print("Mode: auto-delete sessions (default)")
    print(f"Log file: {LOG_FILE}")

    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    cmd = [
        sys.executable, "-m", "uvicorn",
        "webui_llm_proxy.api.server:create_app",
        "--factory",
        "--host", "0.0.0.0",
        "--port", "8080",
        "--log-level", "info",
    ]
    env = os.environ.copy()
    if keep_chat:
        env["PROXY_KEEP_CHAT"] = "true"

    with open(LOG_FILE, "a") as log:
        process = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )

    with open(PID_FILE, "w") as f:
        f.write(str(process.pid))

    print(f"Service started (PID: {process.pid})")
    print("Access: http://0.0.0.0:8080")
    time.sleep(2)

    try:
        with open(LOG_FILE, "r") as f:
            log_content = f.read()[-500:]
        if "Application startup complete" in log_content or "Ready" in log_content:
            print("Service started successfully")
        else:
            print("Service starting, check logs...")
    except Exception:
        pass


def stop() -> None:
    """停止服务"""
    if not os.path.exists(PID_FILE):
        print("Service not running")
        return

    with open(PID_FILE) as f:
        pid = f.read().strip()

    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", pid, "/F"], check=False)
        else:
            os.kill(int(pid), 15)
        print(f"Service stopped (PID: {pid})")
    except Exception as e:
        print(f"Error stopping service: {e}")
    finally:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)


def status() -> None:
    """查看服务状态"""
    if not os.path.exists(PID_FILE):
        print("Service not running")
        return

    with open(PID_FILE) as f:
        pid = f.read().strip()

    try:
        os.kill(int(pid), 0)
        print(f"Service running (PID: {pid})")
    except OSError:
        print(f"Service not running (stale PID file: {pid})")
        os.remove(PID_FILE)


def logs() -> None:
    """查看日志"""
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            content = f.read()
        print(content[-2000:] if len(content) > 2000 else content)
    else:
        print("Log file not found")


def main() -> int:
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="WebUI LLM Proxy Daemon Manager")
    parser.add_argument(
        "command",
        choices=["start", "stop", "restart", "status", "logs"],
        help="Command to execute",
    )
    parser.add_argument("--keep-chat", action="store_true", help="Keep chat sessions after completion")

    args = parser.parse_args()

    if args.command == "start":
        start(keep_chat=args.keep_chat)
    elif args.command == "stop":
        stop()
    elif args.command == "restart":
        stop()
        time.sleep(1)
        start(keep_chat=args.keep_chat)
    elif args.command == "status":
        status()
    elif args.command == "logs":
        logs()

    return 0


if __name__ == "__main__":
    sys.exit(main())
