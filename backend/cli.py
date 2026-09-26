"""`ftth`: one cross-platform entry point for running and maintaining the backend.

    ftth serve          start the HTTP backend (Ctrl+C or `ftth stop` to end)
    ftth stop           stop a backend started by `ftth serve`
    ftth doctor [--fix] check QGIS, worker modules, algorithms, config and data
    ftth config ...     show / set / unset persistent settings
    ftth secret ...     store model API keys in the OS credential store
    ftth plugin ...     install the QGIS plugin into a profile, or package it
    ftth check-model    two-turn tool-calling probe against the configured model
"""
import argparse
import asyncio
import getpass
import json
import logging
import os
import platform
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path

from . import __version__, credentials, logs
from .settings import KEYS, VENDOR, Settings, parse_value, read_config, write_config

logger = logging.getLogger("ftth.cli")
STATE_FILE = "server.json"


def _settings(args, overrides=None):
    return Settings(getattr(args, "data_dir", None), overrides)


def _port_free(host, port):
    with socket.socket() as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _state(settings):
    path = settings.data / STATE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except ValueError:
        return None


def _request(settings, state, method, path, timeout=5):
    import httpx
    return httpx.request(method, state["url"] + path, headers={"Authorization": "Bearer " + settings.token}, timeout=timeout)


def serve(args):
    settings = _settings(args, {"server.host": args.host, "server.port": args.port, "model.provider": args.provider,
        "model.name": args.model, "model.base_url": args.base_url, "model.api": args.api,
        "model.thinking": False if args.no_thinking else None, "log.level": args.log_level})
    if settings.provider == "qwen" and not settings.api_key and sys.stdin.isatty():
        settings.api_key = getpass.getpass("Qwen API Key（隐藏输入；直接回车则不启用模型）: ").strip()
        settings.api_key_source = "prompt" if settings.api_key else ""
        if settings.api_key and args.save_key:
            credentials.store("model", settings.api_key)
            print("已保存到系统凭据库，下次启动无需输入（`ftth secret delete` 可删除）。")
    logfile = logs.configure(settings)
    from .instance_lock import DataDirectoryLock
    # Check before touching server.json, which belongs to any running instance.
    DataDirectoryLock(settings.data).close()
    if not _port_free(settings.host, settings.port):
        print(f"端口 {settings.port} 已被占用。先运行 `ftth stop`，或用 --port 指定其他端口。", file=sys.stderr)
        return 1
    if args.check_model:
        from .model_check import check
        ok, message = asyncio.run(check(settings))
        print(message)
        if not ok:
            print("模型探测未通过，后端未启动。", file=sys.stderr)
            return 1
    elif not settings.model_ready:
        logger.warning("model not configured; data checks, manual runs, display and export remain available")
    import uvicorn
    from .app import create_app
    holder = {}
    app = create_app(settings, on_shutdown_request=lambda: setattr(holder["server"], "should_exit", True))
    server = holder["server"] = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_config=None))
    # Detect QGIS in the background so startup logs say what will run algorithms.
    threading.Thread(target=_announce_runtime, args=(settings,), daemon=True).start()
    state_path = settings.data / STATE_FILE
    state_path.write_text(json.dumps({"pid": os.getpid(), "url": backend_url(settings),
        "version": __version__, "started": time.time(), "log": str(logfile)}), encoding="utf-8")
    print(f"FTTH {__version__} 后端：http://{settings.host}:{settings.port}  数据：{settings.data}  日志：{logfile}")
    print(f"插件连接令牌文件：{settings.data / 'connection.token'}")
    try:
        server.run()
    finally:
        if _state(settings) and _state(settings).get("pid") == os.getpid():
            state_path.unlink(missing_ok=True)
    return 0 if server.started else 1


def _announce_runtime(settings):
    status = settings.runtime_status()
    if status["available"]:
        missing = sorted(set(status["missing"]) - {"qgis", "osgeo"})
        logger.info("algorithms run with QGIS %s (%s): %s", status["qgis_version"], status["layout_label"], status["python"])
        if missing:
            logger.warning("QGIS Python lacks %s (needed by full planning); run `ftth doctor --fix`", ", ".join(missing))
    else:
        logger.warning("%s", status["error"])


def stop(args):
    settings = _settings(args)
    state = _state(settings)
    if not state:
        print("没有记录由 `ftth serve` 启动的后端（数据目录：%s）。前台运行的后端请在其终端按 Ctrl+C。" % settings.data)
        return 1
    import httpx
    try:
        _request(settings, state, "POST", "/shutdown").raise_for_status()
    except httpx.ConnectError:
        (settings.data / STATE_FILE).unlink(missing_ok=True)
        print("记录的后端已不在运行，已清理状态文件。")
        return 0
    except httpx.HTTPStatusError as exc:
        print(f"后端拒绝停止请求：HTTP {exc.response.status_code} {exc.response.text}", file=sys.stderr)
        return 1
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        try:
            _request(settings, state, "GET", "/health", timeout=1)
        except httpx.TransportError:
            print("后端已停止。")
            return 0
        time.sleep(.3)
    print(f"后端在 {args.timeout} 秒内未停止（pid {state.get('pid')}）。", file=sys.stderr)
    return 1


def _schema(settings):
    from .store import SCHEMA_VERSION
    path = settings.data / "app.sqlite"
    if not path.is_file():
        return {"path": str(path), "exists": False, "supported": SCHEMA_VERSION}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
    finally:
        db.close()
    return {"path": str(path), "exists": True, "version": version, "supported": SCHEMA_VERSION}


def doctor(args):
    from .qgis_runtime import INSTALLABLE, QgisUnavailable, candidates, install_missing, probe, worker_packages
    checks = []

    def check(level, name, detail, **extra):
        checks.append({"level": level, "name": name, "detail": detail, **extra})

    try:
        settings = _settings(args)
    except ValueError as exc:
        check("error", "配置", str(exc))
        return _report(args, checks, {})
    check("ok", "版本", f"FTTH {__version__}，Python {platform.python_version()}，{platform.system()} {platform.machine()}")
    configured = {k: v for k, v in settings.effective().items() if v["source"] != "default"}
    check("ok", "配置", f"{settings.config_path}（{'存在' if settings.config_path.is_file() else '不存在，使用默认值'}）",
          overrides=configured)
    runtime = None
    try:
        runtime = settings.runtime(refresh=True)
    except QgisUnavailable as exc:
        check("error", "QGIS", str(exc))
    attempts = [r.describe() for r in settings.runtime_report]
    if runtime:
        check("ok", "QGIS", f"QGIS {runtime.qgis_version}（{runtime.describe()['layout_label']}，来源 {runtime.source}）：{runtime.python}")
        if args.fix and runtime.missing:
            try:
                installed = install_missing(runtime, settings.data)
                if installed:
                    check("ok", "修复", "已安装到 " + str(worker_packages(settings.data)) + "：" + "、".join(installed))
                probe(runtime, [worker_packages(settings.data)])
            except RuntimeError as exc:
                check("error", "修复", str(exc))
        if args.fix and not runtime.source.startswith("config"):
            values = read_config(settings.config_path)
            values["qgis.python"] = str(runtime.python)
            write_config(settings.config_path, values)
            check("ok", "修复", f"已将 qgis.python 固定到 {settings.config_path}，以后启动不再自动探测")
        missing = sorted(set(runtime.missing) - {"qgis", "osgeo"})
        if missing:
            fixable = [m for m in missing if m in INSTALLABLE]
            hint = "运行 `ftth doctor --fix` 安装到应用目录（不修改 QGIS）" if fixable else "需由 QGIS 安装提供"
            check("error", "算法依赖", "缺少 " + "、".join(missing) + "（完整规划需要）；" + hint)
        else:
            check("ok", "算法依赖", "qgis、osgeo、matplotlib、openpyxl 均可导入")
    if args.all:
        tried = {a["python"] for a in attempts}
        for other in candidates():
            if str(other.python) not in tried:
                attempts.append(probe(other, [worker_packages(settings.data)]).describe())
    else:
        tried = {a["python"] for a in attempts}
        attempts += [dict(c.describe(), probed=False) for c in candidates() if str(c.python) not in tried]
    sys.path.insert(0, str(VENDOR))
    try:
        from task_bridge import verify_frozen_algorithms
        integrity = verify_frozen_algorithms(VENDOR)
        check("ok" if integrity.get("status") == "PASS" else "error", "冻结算法", f"{integrity.get('status')}，{integrity.get('file_count')} 个文件哈希校验")
    except Exception as exc:
        check("error", "冻结算法", f"{type(exc).__name__}: {exc}")
    schema = _schema(settings)
    if not schema["exists"]:
        check("ok", "数据库", f"尚未创建（首次启动时建立）：{schema['path']}")
    elif schema["version"] > schema["supported"]:
        check("error", "数据库", f"结构版本 {schema['version']} 高于本程序支持的 {schema['supported']}，请升级后端")
    else:
        pending = schema["supported"] - schema["version"]
        check("ok", "数据库", f"结构版本 {schema['version']}" + (f"，启动时将迁移 {pending} 步并先备份" if pending else "，已是最新"))
    if settings.model_ready:
        source = {"keychain": "系统凭据库", "": "无需密钥"}.get(settings.api_key_source, settings.api_key_source)
        check("ok", "模型", f"{settings.provider} / {settings.model}（{settings.model_api}，密钥来源：{source}）；用 `ftth check-model` 做真实调用探测")
    else:
        check("warning", "模型", "未配置；仍可数据检查、手动规划、展示和导出")
    state = _state(settings)
    if state:
        try:
            health = _request(settings, state, "GET", "/health", timeout=2).json()
            check("ok", "后端", f"运行中 {state['url']}（版本 {health.get('version')}，pid {state.get('pid')}）")
        except Exception:
            check("warning", "后端", f"状态文件记录 {state['url']}，但无法连接；可运行 `ftth stop` 清理")
    elif _port_free(settings.host, settings.port):
        check("ok", "端口", f"{settings.host}:{settings.port} 可用")
    else:
        check("warning", "端口", f"{settings.host}:{settings.port} 已被其他程序占用；用 --port 或 server.port 更换")
    return _report(args, checks, {"qgis_candidates": attempts})


def _report(args, checks, extra):
    failed = any(c["level"] == "error" for c in checks)
    if args.json:
        print(json.dumps({"ok": not failed, "checks": checks, **extra}, ensure_ascii=False, indent=2, default=str))
        return 1 if failed else 0
    marks = {"ok": "[OK]  ", "warning": "[提示]", "error": "[错误]"}
    for item in checks:
        print(f"{marks[item['level']]} {item['name']}：{item['detail']}")
        for key, value in (item.get("overrides") or {}).items():
            print(f"         {key} = {value['value']}（{value['source']}）")
    candidates = extra.get("qgis_candidates") or []
    if candidates:
        print("QGIS 候选（按优先级）：")
        for c in candidates:
            state = "未探测" if c.get("probed") is False else ("可用" if c["usable"] else "不可用 " + json.dumps(c["errors"], ensure_ascii=False)[:160])
            version = f" QGIS {c['qgis_version']}" if c.get("qgis_version") else ""
            print(f"  - {c['python']}（{c['layout_label']}，{c['source']}）{version}：{state}")
    print("结论：" + ("存在需要处理的问题" if failed else "可以运行"))
    return 1 if failed else 0


def config(args):
    settings = _settings(args)
    path = settings.config_path
    if args.action == "path":
        print(path)
        return 0
    if args.action == "show":
        effective = settings.effective()
        if args.json:
            print(json.dumps({"path": str(path), "settings": effective}, ensure_ascii=False, indent=2))
        else:
            print(f"# {path}")
            for key, item in effective.items():
                print(f"{key} = {item['value']!r}  （{item['source']}）")
        return 0
    values = read_config(path)
    value = parse_value(args.key, args.value) if args.action == "set" else None
    if value is None:  # unset, or an empty path
        values.pop(args.key, None)
    else:
        values[args.key] = str(value) if isinstance(value, Path) else value
    write_config(path, values)
    print(f"已更新 {path}；重启后端后生效。")
    return 0


def secret(args):
    if args.action == "status":
        usable, name = credentials.backend()
        print(f"系统凭据库：{name}（{'可用' if usable else '不可用'}）")
        for key, label in credentials.NAMES.items():
            print(f"  {key}（{label}）：{'已保存' if credentials.get(key) else '未保存'}")
        settings = _settings(args)
        print("主模型当前密钥来源：" + ({"keychain": "系统凭据库"}.get(settings.api_key_source, settings.api_key_source) or "无"))
        return 0
    if args.action == "delete":
        print("已删除。" if credentials.delete(args.name) else "凭据库中没有该密钥。")
        return 0
    value = sys.stdin.readline().strip() if args.stdin else getpass.getpass(f"{credentials.NAMES[args.name]} API Key（隐藏输入）: ").strip()
    credentials.store(args.name, value)
    print(f"已保存到系统凭据库（{credentials.backend()[1]}）；环境变量中的密钥仍然优先。")
    return 0


def plugin(args):
    from .distribution import install, package
    if args.action == "package":
        for path in package():
            print(path)
        return 0
    settings = _settings(args)
    connection = {"url": backend_url(settings), "token_path": str(settings.data / "connection.token")}
    target, backup = install(args.profile, args.update, args.profiles_dir, settings.data / "plugin-backups", connection)
    if backup:
        print(f"原插件已备份到 {backup}")
    print(f"已安装到 {target}。重启 QGIS 后在插件管理器中启用 FTTH Agent demo0。")
    print(f"插件将自动连接 {connection['url']}（令牌 {connection['token_path']}）；在插件设置页手动选择的连接优先。")
    return 0


def backend_url(settings):
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "::", "") else settings.host
    return f"http://{host}:{settings.port}"


def check_model(args):
    from .model_check import check
    ok, message = asyncio.run(check(_settings(args)))
    print(message)
    return 0 if ok else (2 if message.startswith("模型未配置") else 1)


def parser():
    root = argparse.ArgumentParser(prog="ftth", description="FTTH 规划后端")
    root.add_argument("--version", action="version", version=f"ftth {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    def command(name, handler, help_text):
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.add_argument("--data-dir", type=Path, help="数据目录（默认 FTTH_DATA_DIR 或仓库下 runtime）")
        sub.set_defaults(handler=handler)
        return sub

    sub = command("serve", serve, "启动后端")
    sub.add_argument("--host")
    sub.add_argument("--port", type=int)
    sub.add_argument("--provider", choices=["qwen", "openai-compatible"])
    sub.add_argument("--model")
    sub.add_argument("--base-url")
    sub.add_argument("--api", choices=["chat", "responses"])
    sub.add_argument("--no-thinking", action="store_true", help="关闭模型思考")
    sub.add_argument("--check-model", action="store_true", help="启动前做一次真实工具调用探测，失败则不启动")
    sub.add_argument("--log-level", choices=["debug", "info", "warning", "error"])
    sub.add_argument("--save-key", action="store_true", help="把隐藏输入的密钥保存到系统凭据库")
    sub = command("stop", stop, "停止由 ftth serve 启动的后端")
    sub.add_argument("--timeout", type=float, default=30)
    sub = command("doctor", doctor, "检查 QGIS、算法依赖、冻结算法、配置与数据")
    sub.add_argument("--fix", action="store_true", help="安装缺失的纯 Python 依赖到应用目录，并固定检测到的 QGIS")
    sub.add_argument("--all", action="store_true", help="探测全部 QGIS 候选")
    sub.add_argument("--json", action="store_true")
    sub = command("config", config, "查看或修改持久配置")
    sub.add_argument("action", choices=["show", "path", "set", "unset"])
    sub.add_argument("key", nargs="?", choices=list(KEYS), metavar="KEY")
    sub.add_argument("value", nargs="?")
    sub.add_argument("--json", action="store_true")
    sub = command("secret", secret, "在系统凭据库中保存、删除或查看模型密钥")
    sub.add_argument("action", choices=["set", "delete", "status"])
    sub.add_argument("name", nargs="?", default="model", choices=list(credentials.NAMES))
    sub.add_argument("--stdin", action="store_true", help="从标准输入读取密钥（脚本化使用，勿写入命令行参数）")
    sub = command("plugin", plugin, "安装或打包 QGIS 插件")
    sub.add_argument("action", choices=["install", "package"])
    sub.add_argument("--profile", default="default")
    sub.add_argument("--update", action="store_true", help="已安装时先备份再替换")
    sub.add_argument("--profiles-dir", type=Path, help="QGIS 配置根目录（默认按平台约定）")
    command("check-model", check_model, "真实模型工具调用探测")
    return root


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # Legacy code pages must not crash output.
    args = parser().parse_args(argv)
    if args.command == "config" and args.action in {"set", "unset"} and not args.key:
        parser().error("config set/unset 需要 KEY")
    if args.command == "config" and args.action == "set" and args.value is None:
        parser().error("config set 需要 VALUE")
    try:
        return args.handler(args)
    except (ValueError, RuntimeError) as exc:  # Actionable messages, not tracebacks.
        print(f"错误：{exc}", file=sys.stderr)
        return 1
