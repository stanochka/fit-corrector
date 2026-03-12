#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def find_open_port(start: int = 8501, end: int = 8599) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            in_use = s.connect_ex(("127.0.0.1", port)) == 0
            if not in_use:
                return port
    return 8601


def wait_for_streamlit(base_url: str, timeout_s: float = 20.0) -> bool:
    deadline = time.time() + timeout_s
    health_url = f"{base_url}/_stcore/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1.5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def app_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "FIT Corrector"


def ensure_venv(root: Path) -> Path:
    support = app_support_dir()
    support.mkdir(parents=True, exist_ok=True)
    venv_dir = support / ".venv_app"
    py = venv_dir / "bin" / "python3"
    if not py.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])
    subprocess.check_call([str(py), "-m", "pip", "install", "-U", "pip"], cwd=str(root))
    subprocess.check_call([str(py), "-m", "pip", "install", "-r", str(root / "requirements.txt")], cwd=str(root))
    return py


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    logs_dir = app_support_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    launcher_log = logs_dir / "app_launcher.log"
    with launcher_log.open("a", encoding="utf-8") as f:
        f.write(f"launch start pid={os.getpid()}\\n")
    py = ensure_venv(root)

    port = find_open_port()
    base_url = f"http://127.0.0.1:{port}"

    log_file = logs_dir / "streamlit.log"
    with log_file.open("ab") as log:
        proc = subprocess.Popen(
            [
                str(py),
                "-m",
                "streamlit",
                "run",
                str(root / "streamlit_app.py"),
                "--server.headless=true",
                f"--server.port={port}",
                "--browser.gatherUsageStats=false",
            ],
            cwd=str(root),
            stdout=log,
            stderr=log,
            start_new_session=True,
        )

    if wait_for_streamlit(base_url):
        subprocess.Popen(["open", base_url], start_new_session=True)
        return 0

    # fallback: open logs if startup failed
    subprocess.Popen(["open", str(log_file)], start_new_session=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
