"""Offline SSE -> real backend -> Qt HTTP client acceptance, optionally installed ZIP."""
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import uvicorn
from backend.app import create_app
from backend.distribution import install, ui_test_env
from backend.settings import Settings
from test_streaming import streaming_model

with tempfile.TemporaryDirectory(prefix="ftth-stream-ui-") as directory:
    settings = Settings(directory)
    text = "## 数据检查\n\n这是一段**合成数据的流式验证**，用于检查新版侧栏。\n\n模型正文按接收顺序显示，工具状态与回复分开呈现。\n\n- 道路来源：由用户确认\n- 楼宇拆分：沿用已保存策略\n- 执行结果：以算法报告为准\n\n你可以切换会话再回来，已经收到的正文会恢复。"
    model = streaming_model(settings, content_parts=[text[i:i+5] for i in range(0, len(text), 5)])
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(settings, model=model), log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        env = settings.worker_env()
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        env.update(DEMO_TEST_URL=url, DEMO_TEST_TOKEN=settings.token, PYTHONFAULTHANDLER="1")
        env.update(ui_test_env(directory, sys.argv))
        if "--fresh-install" in sys.argv:
            # Exactly what `ftth plugin install` does, into a throwaway profile.
            target, _ = install(profiles=Path(directory) / "profiles", backups=Path(directory) / "backups",
                connection={"url": url, "token_path": str(settings.data / "connection.token")})
            env.update(DEMO_TEST_PLUGINS=str(target.parent), DEMO_TEST_EXPECT_AUTOCONNECT="1")
        result = subprocess.run([str(settings.qgis_python), "-u", str(ROOT / "scripts/streaming_ui_smoke.py")],
            env=env, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        print(result.stdout)
        print(result.stderr)
        assert result.returncode == 0, f"Qt process exit: {result.returncode}"
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
