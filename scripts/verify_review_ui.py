"""Isolated HTTP backend + real QGIS Qt review UI acceptance (scripted model)."""
import asyncio
import os
from pathlib import Path
import shutil
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
from backend.distribution import ui_test_env
from test_review import fixture_run, ReviewModel
from test_workflow import IntentModel
from backend.models import PolicyInput

with tempfile.TemporaryDirectory(prefix="ftth-review-ui-") as directory:
    service, sid, _, run, _ = fixture_run(Path(directory))
    workflow_ui = '--workflow-ui' in sys.argv
    if workflow_ui:
        service.set_policy(sid, PolicyInput(use_formal_infrastructure=True, use_recognition_roads=True, split_oversized_imbs=True))
    settings = service.settings
    asyncio.run(service.close())
    service.store.db.close()
    shutil.copy2(Path(directory) / "test.sqlite", Path(directory) / "app.sqlite")
    settings.model = settings.api_key = settings.base_url = ""
    for key in list(os.environ):
        if key.startswith("FTTH_REVIEW_"):
            os.environ.pop(key)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    application = create_app(settings, model=IntentModel() if workflow_ui else None, review_model=ReviewModel())
    server = uvicorn.Server(uvicorn.Config(application, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        if workflow_ui:
            async def hold_algorithm(run_id):
                await asyncio.sleep(300)  # UI acceptance only; real algorithm is covered by verify_reviews --workflow.
            application.state.service.execute = hold_algorithm
        env = settings.worker_env()
        env.update(DEMO_TEST_URL=f"http://127.0.0.1:{sock.getsockname()[1]}", DEMO_TEST_TOKEN=settings.token,
            DEMO_TEST_SESSION=sid, DEMO_TEST_RUN=run["id"])
        env.update(ui_test_env(directory, sys.argv))
        if workflow_ui:
            env['DEMO_TEST_WORKFLOW'] = '1'
        result = subprocess.run([str(settings.qgis_python), "-u", str(ROOT / "scripts/review_ui_smoke.py")],
            env=env, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        print(result.stdout)
        print(result.stderr)
        assert result.returncode == 0
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
