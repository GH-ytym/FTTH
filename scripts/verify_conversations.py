"""Launch a temporary loopback service with a fake model and real QGIS UI client."""
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import uvicorn
from backend.settings import Settings
from backend.app import create_app
from backend.distribution import ui_test_env
from test_agent import FakeToolModel

with tempfile.TemporaryDirectory(prefix="ftth-ui-") as directory:
    settings = Settings(Path(directory))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(settings, model=FakeToolModel()), log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        env = settings.worker_env()
        env.update(DEMO_TEST_URL=f"http://127.0.0.1:{port}", DEMO_TEST_TOKEN=settings.token)
        env.update(ui_test_env(directory, sys.argv))
        result = subprocess.run([str(settings.qgis_python), str(ROOT / "scripts/conversation_ui_smoke.py")],
            env=env, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        print(result.stdout)
        print(result.stderr)
        assert result.returncode == 0
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
