"""Entry point that launches the Streamlit web interface."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    web_app = Path(__file__).with_name("web.py")
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(web_app),
        "--server.headless", "false",
        "--browser.gatherUsageStats", "false",
    ]
    sys.exit(subprocess.call(cmd))
