"""Configuration, QGIS runtime resolution, migrations, logging and the `ftth` CLI.

QGIS layouts are fake directory trees; a fake ``qgis`` package placed in the
app-owned worker directory proves the real probe subprocess end to end.
"""
import configparser
import json
import logging
import os
import socket
import sqlite3
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

import backend.qgis_runtime as qr
from backend import __version__
from backend.cli import main
from backend.distribution import install, package
from backend.settings import Settings, write_config
from backend.store import SCHEMA_VERSION, Store

ROOT = Path(__file__).resolve().parents[1]
real_candidates = qr.candidates  # conftest disables discovery for every test


def touch(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def fake_osgeo4w(root, python="Python312", qt="Qt5"):
    touch(root / "bin/python.exe")
    (root / "apps/qgis-ltr/python/qgis").mkdir(parents=True)
    (root / "apps" / python).mkdir(parents=True)
    (root / "apps" / qt / "plugins").mkdir(parents=True)
    return root


def fake_bundle(root):
    touch(root / "Contents/MacOS/python", "#!/bin/sh\n")
    return root


def fake_conda(root):
    touch(root / "conda-meta/qgis-3.40.1-py312_0.json", "{}")
    touch(root / ("python.exe" if os.name == "nt" else "bin/python"))
    return root


def fake_qgis_package(directory, version="3.99.0-Fake"):
    touch(directory / "qgis/__init__.py")
    touch(directory / "qgis/core.py", f"class Qgis:\n    QGIS_VERSION = {version!r}\n")
    touch(directory / "osgeo/__init__.py")
    touch(directory / "osgeo/ogr.py")


def test_layouts_are_inferred_from_install_roots(tmp_path):
    osgeo = qr.runtime_for_root(fake_osgeo4w(tmp_path / "OSGeo4W", "Python313", "Qt6"), "test")
    assert osgeo.layout == "osgeo4w" and osgeo.python == tmp_path / "OSGeo4W/bin/python.exe"
    env = osgeo.env({"PATH": "base", "PYTHONPATH": "leaked"}, [tmp_path / "extra"])
    assert env["PYTHONHOME"] == str(tmp_path / "OSGeo4W/apps/Python313")
    assert env["QGIS_PREFIX_PATH"] == str(tmp_path / "OSGeo4W/apps/qgis-ltr")
    assert env["PYTHONPATH"] == os.pathsep.join([str(tmp_path / "OSGeo4W/apps/qgis-ltr/python"), str(tmp_path / "extra")])
    assert str(tmp_path / "OSGeo4W/apps/Qt6/plugins") in env["QT_PLUGIN_PATH"].split(os.pathsep)
    assert env["PATH"].endswith(os.pathsep + "base") and env["QT_QPA_PLATFORM"] == "offscreen"

    bundle = qr.runtime_for_root(fake_bundle(tmp_path / "QGIS-LTR.app"), "test")
    env = bundle.env({"PYTHONHOME": "/elsewhere", "PYTHONPATH": "leaked"})
    assert bundle.layout == "macos-bundle" and bundle.python.name == "python"
    assert "PYTHONHOME" not in env and "PYTHONPATH" not in env

    conda = qr.runtime_for_root(fake_conda(tmp_path / "envs/gis"), "test")
    assert conda.layout == "conda" and conda.env({})["CONDA_PREFIX"] == str(tmp_path / "envs/gis")
    assert qr.runtime_for_root(tmp_path / "envs", "test") is None


def test_explicit_interpreter_finds_its_install(tmp_path):
    fake_osgeo4w(tmp_path / "osgeo")
    touch(tmp_path / "osgeo/apps/Python312/python.exe")
    runtime = qr.runtime_for_python(tmp_path / "osgeo/apps/Python312/python.exe", "config")
    assert (runtime.layout, runtime.root) == ("osgeo4w", tmp_path / "osgeo")
    fake_bundle(tmp_path / "QGIS.app")
    assert qr.runtime_for_python(tmp_path / "QGIS.app/Contents/MacOS/python", "config").root == tmp_path / "QGIS.app"
    assert qr.runtime_for_python(Path("/usr/bin/python3"), "config").layout == "system"
    with pytest.raises(qr.QgisUnavailable, match="ftth doctor"):
        qr.resolve(explicit_root=tmp_path / "not-qgis")


def test_discovery_order_is_path_then_conda_then_standard_locations(tmp_path, monkeypatch):
    on_path = fake_conda(tmp_path / "path-env")
    registered = fake_conda(tmp_path / "registered-env")
    standard = fake_bundle(tmp_path / "QGIS.app")
    touch(on_path / "bin/qgis_process")
    monkeypatch.setattr(qr.shutil, "which", lambda name: str(on_path / "bin/qgis_process") if name == "qgis_process" else None)
    monkeypatch.setattr(qr, "_conda_envs", lambda: [registered, on_path])
    monkeypatch.setattr(qr, "_standard_roots", lambda: [standard, tmp_path / "missing.app"])
    found = real_candidates()
    assert [(r.root, r.source) for r in found] == [(on_path, "path:qgis_process"), (registered, "conda"), (standard, "standard-location")]


def test_probe_and_worker_env_use_app_owned_packages(tmp_path):
    data = tmp_path / "data"
    fake_qgis_package(qr.worker_packages(data))
    settings = Settings(data, {"qgis.python": sys.executable})
    runtime = settings.runtime()
    assert runtime.usable and runtime.qgis_version == "3.99.0-Fake" and runtime.source == "config:qgis.python"
    assert runtime.python_version == ".".join(map(str, sys.version_info[:3]))
    env = settings.worker_env()
    assert str(qr.worker_packages(data)) in env["PYTHONPATH"].split(os.pathsep)
    status = settings.runtime_status()
    assert status["available"] and status["qgis_version"] == "3.99.0-Fake"


def test_unusable_configured_interpreter_explains_itself(tmp_path):
    settings = Settings(tmp_path, {"qgis.python": sys.executable})
    with pytest.raises(qr.QgisUnavailable, match="配置的 QGIS 不可用") as error:
        settings.runtime()
    assert "ftth doctor" in str(error.value)
    assert settings.runtime_report[0].missing[0] == "qgis"
    assert settings.runtime_status()["available"] is False


def test_settings_layering_sources_and_validation(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    write_config(config, {"run.timeout": 120, "server.port": 9000, "qgis.root": "D:\\QGIS 3.40\\中文"})
    assert tomllib.loads(config.read_text(encoding="utf-8"))["qgis"]["root"] == "D:\\QGIS 3.40\\中文"
    monkeypatch.setenv("FTTH_PORT", "9100")
    settings = Settings(tmp_path, {"run.timeout": 30})
    assert (settings.timeout, settings.port, settings.host) == (30, 9100, "127.0.0.1")
    assert settings.sources["run.timeout"] == "cli" and settings.sources["server.port"] == "env:FTTH_PORT"
    assert settings.sources["qgis.root"] == "file" and settings.effective()["qgis.root"]["value"].endswith("中文")
    for text, message in (('[model]\napi_key = "x"\n', "不能保存密钥"), ('[model]\nnmae = "x"\n', "未知配置项"),
                          ('[run]\ntimeout = "soon"\n', "必须为整数"), ("[run\n", "格式错误")):
        config.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            Settings(tmp_path)


def test_schema_migrations_back_up_existing_data_and_refuse_newer(tmp_path):
    fresh = Store(tmp_path / "fresh.sqlite")
    assert fresh.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    fresh.db.close()
    assert not list(tmp_path.glob("fresh.sqlite.*.bak"))
    legacy = sqlite3.connect(tmp_path / "v060.sqlite")
    for table in ("objects (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id))",
                  "events (id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, data TEXT)",
                  "requests (session TEXT, key TEXT, value TEXT, PRIMARY KEY(session,key))",
                  "workflow_actions (session TEXT, owner TEXT, action TEXT, digest TEXT, run_id TEXT, PRIMARY KEY(session,owner,action))"):
        legacy.execute("CREATE TABLE " + table)
    legacy.execute("""INSERT INTO objects VALUES ('run', 'r', '{"id": "r", "session_id": "s"}')""")
    for kind in ("user", "assistant_stream", "assistant_stream", "assistant"):
        legacy.execute("INSERT INTO events(session, data) VALUES ('s', ?)", (json.dumps({"type": kind, "message_id": "m"}),))
    legacy.commit()
    legacy.close()
    upgraded = Store(tmp_path / "v060.sqlite")
    assert upgraded.find("run", "s")[0]["id"] == "r"
    indexes = {r[0] for r in upgraded.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"objects_session", "events_session"} <= indexes
    assert [e["type"] for e in upgraded.events("s", 0)] == ["user", "assistant"]
    upgraded.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    upgraded.db.commit()
    upgraded.db.close()
    backups = list(tmp_path.glob("v060.sqlite.pre-schema-v*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as copy:
        assert copy.execute("SELECT id FROM objects").fetchall() == [("r",)]
        assert copy.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 4
    with pytest.raises(RuntimeError, match="高于本程序支持"):
        Store(tmp_path / "v060.sqlite")


def test_logs_redact_secrets_including_tracebacks(tmp_path, monkeypatch):
    from backend import logs
    monkeypatch.setenv("FTTH_API_KEY", "sk-live-0123456789abcdef")
    settings = Settings(tmp_path)
    path = logs.configure(settings, console=False)
    logger = logging.getLogger("ftth.test")
    try:
        raise RuntimeError("provider said api_key=sk-live-0123456789abcdef; Authorization: Bearer " + settings.token)
    except RuntimeError:
        logger.warning("model failed", exc_info=True)
    for handler in logging.getLogger("ftth").handlers:
        handler.flush()
    text = path.read_text(encoding="utf-8")
    assert "model failed" in text and "RuntimeError" in text
    assert "0123456789abcdef" not in text and settings.token not in text
    logs.configure(settings, console=False)
    assert len([h for h in logging.getLogger("ftth").handlers if h.get_name() == logs.HANDLER_NAME]) == 1


def test_cli_config_roundtrip(tmp_path, capsys):
    data = ["--data-dir", str(tmp_path)]
    assert main(["config", "set", "run.timeout", "90", *data]) == 0
    assert main(["config", "set", "model.thinking", "false", *data]) == 0
    assert main(["config", "set", "run.timeout", "soon", *data]) == 1
    assert "必须为整数" in capsys.readouterr().err
    assert main(["config", "show", "--json", *data]) == 0
    shown = json.loads(capsys.readouterr().out)["settings"]
    assert shown["run.timeout"] == {"value": 90, "source": "file"}
    assert shown["model.thinking"] == {"value": False, "source": "file"}
    assert main(["config", "unset", "run.timeout", *data]) == 0
    assert Settings(tmp_path).sources["run.timeout"] == "default"
    assert main(["config", "set", "qgis.python", "/opt/qgis/python", *data]) == 0
    assert main(["config", "set", "qgis.python", "", *data]) == 0
    assert "qgis" not in tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))


def test_serve_refuses_a_data_dir_owned_by_a_running_backend(tmp_path, capsys):
    from backend.instance_lock import DataDirectoryLock
    (tmp_path / "server.json").write_text('{"pid": 1, "url": "http://127.0.0.1:1"}', encoding="utf-8")
    lock = DataDirectoryLock(tmp_path)
    try:
        assert main(["serve", "--data-dir", str(tmp_path), "--port", "0"]) == 1
    finally:
        lock.close()
    assert "已有后端运行" in capsys.readouterr().err
    assert json.loads((tmp_path / "server.json").read_text(encoding="utf-8"))["pid"] == 1


def test_plugin_install_backs_up_and_replaces(tmp_path):
    profiles, backups = tmp_path / "profiles", tmp_path / "backups"
    target, backup = install("default", profiles=profiles, backups=backups)
    assert (target / "metadata.txt").is_file() and backup is None
    assert not list(target.rglob("__pycache__")) and not list(target.rglob("*.pyc"))
    touch(target / "stale.py")
    with pytest.raises(ValueError, match="--update"):
        install("default", profiles=profiles, backups=backups)
    target, backup = install("default", update=True, profiles=profiles, backups=backups)
    assert (backup / "stale.py").is_file() and not (target / "stale.py").exists()
    with pytest.raises(ValueError, match="配置名"):
        install("../escape", profiles=profiles)


def test_package_writes_versioned_archive_and_manifest(tmp_path):
    output, versioned, release = package(tmp_path)
    manifest = json.loads(release.read_text(encoding="utf-8"))
    assert versioned.name == f"ftth_demo0-{__version__}.zip" and manifest["version"] == __version__
    assert "backend/cli.py" in manifest["files"] and output.is_file()


def test_single_version_source():
    metadata = configparser.ConfigParser()
    metadata.read(ROOT / "qgis_plugin/ftth_demo0/metadata.txt", encoding="utf-8")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert __version__ == project == metadata["general"]["version"]


def test_doctor_reports_fake_qgis_and_missing_worker_modules(tmp_path, capsys):
    fake_qgis_package(qr.worker_packages(tmp_path))
    write_config(tmp_path / "config.toml", {"qgis.python": sys.executable})
    code = main(["doctor", "--json", "--data-dir", str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    checks = {c["name"]: c for c in report["checks"]}
    assert checks["QGIS"]["level"] == "ok" and "3.99.0-Fake" in checks["QGIS"]["detail"]
    assert checks["冻结算法"]["level"] == "ok" and checks["数据库"]["level"] == "ok"
    missing = {m for m in ("matplotlib", "openpyxl") if m in checks["算法依赖"]["detail"]}
    assert (code == 1) == bool(missing) and (checks["算法依赖"]["level"] == "error") == bool(missing)


def test_serve_and_stop_lifecycle(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("FTTH_") and k not in ("DASHSCOPE_API_KEY", "FTTH_API_KEY")}
    env["FTTH_QGIS_PYTHON"] = str(tmp_path / "no-qgis")  # Hermetic: skip discovery of a real QGIS.
    # A file, not an unread PIPE: a full pipe buffer (about 4 KB on Windows) blocks
    # the server's shutdown logging, so it never exits (seen on Windows CI).
    console = tmp_path / "serve-console.txt"
    output = console.open("wb")
    server = subprocess.Popen([sys.executable, "-m", "backend", "serve", "--data-dir", str(tmp_path), "--port", str(port)],
                              cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
    try:
        import httpx
        token = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                token = (tmp_path / "connection.token").read_text(encoding="utf-8").strip()
                health = httpx.get(f"http://127.0.0.1:{port}/health", headers={"Authorization": "Bearer " + token}, timeout=1).json()
                break
            except (OSError, httpx.TransportError):
                time.sleep(.2)
        else:
            pytest.fail("backend did not start: " + console.read_text(encoding="utf-8", errors="replace"))
        assert health["version"] == __version__ and health["qgis"]["available"] is False
        # On Windows the venv python.exe is a launcher; the pid is the real server's.
        state = json.loads((tmp_path / "server.json").read_text(encoding="utf-8"))
        assert state["url"] == f"http://127.0.0.1:{port}" and state["pid"] > 0
        assert main(["stop", "--data-dir", str(tmp_path)]) == 0
        assert server.wait(60) == 0, console.read_text(encoding="utf-8", errors="replace")
        assert not (tmp_path / "server.json").exists()
        assert "FTTH backend stopped" in (tmp_path / "logs/backend.log").read_text(encoding="utf-8")
        assert token not in (tmp_path / "logs/backend.log").read_text(encoding="utf-8")
        assert main(["stop", "--data-dir", str(tmp_path)]) == 1
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
        output.close()


def test_probe_never_receives_model_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("FTTH_API_KEY", "secret-value")
    runtime = qr.QgisRuntime(Path(sys.executable), "system", tmp_path, "test")
    touch(tmp_path / "pkgs/sitecustomize.py", "import os, pathlib\npathlib.Path(%r).write_text(os.environ.get('FTTH_API_KEY', 'absent'))\n" % str(tmp_path / "seen.txt"))
    qr.probe(runtime, [tmp_path / "pkgs"])
    assert (tmp_path / "seen.txt").read_text() == "absent"


def test_runtime_refresh_endpoint_and_unmanaged_shutdown(tmp_path):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    settings = Settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + settings.token
        assert client.get("/health").json()["qgis"]["available"] is False
        # QGIS becomes available after startup; the cached failure persists until refreshed.
        fake_qgis_package(qr.worker_packages(tmp_path))
        settings.qgis_python_config = Path(sys.executable)
        assert client.get("/health").json()["qgis_python_exists"] is False
        refreshed = client.post("/runtime/refresh").json()
        assert refreshed["available"] and refreshed["qgis_version"] == "3.99.0-Fake"
        assert client.get("/health").json()["qgis"]["qgis_version"] == "3.99.0-Fake"
        assert client.post("/shutdown").status_code == 409


def plugin_module(name):
    """Load a Qt-free plugin module without importing the QGIS plugin package."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ftth_plugin_" + name, ROOT / "qgis_plugin/ftth_demo0" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_connection_prefers_saved_choice_then_install_defaults(tmp_path):
    connection = plugin_module("connection")
    installed = touch(tmp_path / "connection.json", json.dumps({"url": "http://127.0.0.1:9000", "token_path": str(touch(tmp_path / "installed.token", "installed\n"))}))
    saved = touch(tmp_path / "saved.token", "saved")
    assert connection.resolve(None, None, installed) == ("http://127.0.0.1:9000", str(tmp_path / "installed.token"))
    assert connection.resolve("http://h:1", str(saved), installed) == ("http://h:1", str(saved))
    # A saved token file that no longer exists falls back to the install record.
    assert connection.resolve("", str(tmp_path / "gone.token"), installed)[1] == str(tmp_path / "installed.token")
    assert connection.read_token(str(tmp_path / "installed.token")) == "installed" and connection.read_token(None) == ""
    touch(tmp_path / "broken.json", "{")
    assert connection.resolve(None, None, tmp_path / "broken.json") == (connection.DEFAULT_URL, None)


def test_plugin_reports_backend_qgis_problems():
    problem = plugin_module("connection").backend_problem
    assert problem(None) == "" and problem({"available": True, "missing": []}) == ""
    assert "ftth doctor" in problem({"available": False, "error": "未找到"})
    assert "openpyxl" in problem({"available": True, "missing": ["openpyxl"]}) and "--fix" in problem({"available": True, "missing": ["openpyxl"]})


def test_install_records_connection_and_packaging_never_ships_it(tmp_path, monkeypatch):
    import shutil
    import zipfile
    import backend.distribution as distribution
    source = tmp_path / "source/ftth_demo0"
    shutil.copytree(distribution.SOURCE, source, ignore=shutil.ignore_patterns("__pycache__"))
    touch(source / "connection.json", '{"token_path": "developer-machine"}')
    monkeypatch.setattr(distribution, "SOURCE", source)
    _, versioned, _ = distribution.package(tmp_path / "dist")
    assert not [n for n in zipfile.ZipFile(versioned).namelist() if n.endswith("connection.json")]
    target, _ = distribution.install(profiles=tmp_path / "profiles", connection={"url": "http://127.0.0.1:8765", "token_path": "/data/connection.token"})
    assert json.loads((target / "connection.json").read_text(encoding="utf-8"))["token_path"] == "/data/connection.token"


def test_model_key_precedence_and_secret_commands(tmp_path, monkeypatch, capsys, isolate_credentials):
    import io
    from backend.credentials import SERVICE
    data = ["--data-dir", str(tmp_path)]
    assert Settings(tmp_path).api_key_source == ""
    monkeypatch.setattr(sys, "stdin", io.StringIO("stored-key-123\n"))
    assert main(["secret", "set", "--stdin", *data]) == 0
    assert isolate_credentials.values[(SERVICE, "model")] == "stored-key-123"
    settings = Settings(tmp_path)
    assert (settings.api_key, settings.api_key_source) == ("stored-key-123", "keychain")
    monkeypatch.setenv("FTTH_API_KEY", "env-key-456")
    assert Settings(tmp_path).api_key_source == "env:FTTH_API_KEY"  # the environment wins
    assert main(["secret", "status", *data]) == 0
    status = capsys.readouterr().out
    assert "已保存" in status and "stored-key-123" not in status and "env-key-456" not in status
    assert main(["secret", "delete", *data]) == 0 and (SERVICE, "model") not in isolate_credentials.values
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    assert main(["secret", "set", "--stdin", *data]) == 1


def test_review_key_from_credential_store(tmp_path, monkeypatch, isolate_credentials):
    from backend.credentials import SERVICE
    from backend.review import review_settings
    monkeypatch.setenv("FTTH_BASE_URL", "https://main.invalid/v1")
    monkeypatch.setenv("FTTH_REVIEW_BASE_URL", "https://review.invalid/v1")
    with pytest.raises(ValueError, match="FTTH_REVIEW_API_KEY"):
        review_settings(Settings(tmp_path))
    isolate_credentials.values[(SERVICE, "review")] = "review-key"
    assert review_settings(Settings(tmp_path)).api_key == "review-key"
