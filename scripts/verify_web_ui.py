import os, sys, socket, threading, time, tempfile, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.settings import Settings
from backend.app import create_app
from backend.distribution import profiles_dir
import uvicorn

with tempfile.TemporaryDirectory(prefix="ftth-web-") as folder:
    settings = Settings(Path(folder))
    settings.model = settings.api_key = settings.base_url = ""
    app = create_app(settings)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    runtime = settings.runtime()
    env = runtime.env(os.environ)
    env["QT_QPA_PLATFORM"] = "windows"
    for k, v in {
        "PREFIX": "",
        "BINARIES": "bin",
        "PLUGINS": "plugins",
        "LIBRARIES": "lib",
        "TRANSLATIONS": "translations",
        "HEADERS": "include",
        "DOC": "doc",
    }.items():
        env["O4W_QT_" + k] = (runtime.root / "apps/Qt5" / v).as_posix()
    token = Path(folder) / "ui.token"
    token.write_text(settings.token)
    if '--installed' in sys.argv:
        env['DEMO_TEST_PLUGINS'] = str(profiles_dir() / 'default/python/plugins')
    env.update(
        FTTH_PLUGIN_SETTINGS=str(Path(folder) / "ui.ini"),
        DEMO_TEST_URL="http://127.0.0.1:" + str(sock.getsockname()[1]),
        DEMO_TEST_TOKEN_FILE=str(token),
        DEMO_TEST_SECRET=settings.token,
    )
    try:
        p = subprocess.run(
            [str(runtime.python), str(ROOT / "scripts/web_ui_smoke.py")],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        print(p.stdout)
        print(p.stderr)
        assert p.returncode == 0
    finally:
        server.should_exit = True
        thread.join(5)
