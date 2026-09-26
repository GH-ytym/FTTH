"""Layered settings: defaults < config file < FTTH_* environment < command line.

The config file is TOML (``<data>/config.toml`` or ``FTTH_CONFIG``). Secrets are
never read from it: model keys come from the environment, the OS credential
store (`ftth secret set`) or a hidden prompt.
"""
import json
import os
import secrets
import threading
import time
import tomllib
from pathlib import Path

from . import credentials
from .qgis_runtime import QgisUnavailable, resolve, worker_packages

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "ftth"
QWEN_BASE_URL = "https://maas.qianwenaiapi.com/compatible-mode/v1"
QWEN_MODEL = "qwen3.8-flash"


def _bool(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text not in {"true", "false", "1", "0"}:
        raise ValueError("必须为 true 或 false")
    return text in {"true", "1"}


def _choice(*options):
    def parse(value):
        text = str(value).strip().lower()
        if text not in options:
            raise ValueError("必须为 " + " 或 ".join(options))
        return text
    return parse


def _int(value):
    if isinstance(value, bool) or not str(value).strip().lstrip("-").isdigit():
        raise ValueError("必须为整数")
    return int(value)


def _path(value):
    return Path(str(value).strip()).expanduser() if str(value).strip() else None


def _text(value):
    return str(value).strip()


# dotted key: (environment variable, parser, default, attribute)
KEYS = {
    "qgis.python": ("FTTH_QGIS_PYTHON", _path, None, "qgis_python_config"),
    "qgis.root": ("FTTH_QGIS_ROOT", _path, None, "qgis_root_config"),
    "model.provider": ("FTTH_PROVIDER", lambda v: _text(v).lower(), None, "provider"),
    "model.name": ("FTTH_MODEL", _text, None, "model"),
    "model.base_url": ("FTTH_BASE_URL", _text, None, "base_url"),
    "model.api": ("FTTH_MODEL_API", _choice("chat", "responses"), "chat", "model_api"),
    "model.thinking": ("FTTH_ENABLE_THINKING", _bool, True, "enable_thinking"),
    "run.timeout": ("FTTH_RUN_TIMEOUT", _int, 3600, "timeout"),
    "server.host": ("FTTH_HOST", _text, "127.0.0.1", "host"),
    "server.port": ("FTTH_PORT", _int, 8765, "port"),
    "log.level": ("FTTH_LOG_LEVEL", _choice("debug", "info", "warning", "error"), "info", "log_level"),
}


def config_path(data):
    explicit = os.environ.get("FTTH_CONFIG")
    return Path(explicit).expanduser() if explicit else Path(data) / "config.toml"


def read_config(path):
    """Flatten a TOML file into dotted keys, rejecting unknown keys and secrets."""
    if not path.is_file():
        return {}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"配置文件 {path} 格式错误：{exc}") from exc
    values = {}
    for section, table in raw.items():
        if not isinstance(table, dict):
            raise ValueError(f"配置文件 {path}：{section} 必须写成表（[{section}]）")
        for name, value in table.items():
            key = section + "." + name
            if any(word in name for word in ("key", "token", "secret", "password")):
                raise ValueError(f"配置文件 {path} 不能保存密钥 {key}；请使用环境变量 FTTH_API_KEY，或由 `ftth serve` 隐藏输入")
            if key not in KEYS:
                raise ValueError(f"配置文件 {path} 含未知配置项 {key}；可用项：{', '.join(KEYS)}")
            values[key] = value
    return values


def write_config(path, values):
    """Persist dotted keys as TOML; strings are JSON-escaped, which TOML accepts."""
    sections = {}
    for key in sorted(values):
        section, name = key.split(".", 1)
        sections.setdefault(section, []).append((name, values[key]))
    lines = ["# FTTH backend settings (ftth config set/unset). Secrets never belong here.", ""]
    for section, items in sections.items():
        lines.append(f"[{section}]")
        for name, value in items:
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, int):
                rendered = str(value)
            else:
                rendered = json.dumps(str(value), ensure_ascii=False)
            lines.append(f"{name} = {rendered}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temporary, path)


def parse_value(key, value):
    if key not in KEYS:
        raise ValueError(f"未知配置项 {key}；可用项：{', '.join(KEYS)}")
    try:
        return KEYS[key][1](value)
    except ValueError as exc:
        raise ValueError(f"{key}：{exc}") from exc


class Settings:
    def __init__(self, data=None, overrides=None):
        self.data = Path(data or os.environ.get("FTTH_DATA_DIR") or ROOT / "runtime").expanduser().resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        token_file = self.data / "connection.token"
        if not token_file.exists():
            token_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        self.token = token_file.read_text(encoding="utf-8").strip()
        self.config_path = config_path(self.data)
        layers = [("file", read_config(self.config_path)),
                  ("env", {key: os.environ[env] for key, (env, *_) in KEYS.items() if os.environ.get(env, "").strip()}),
                  ("cli", {k: v for k, v in (overrides or {}).items() if v is not None})]
        self.sources = {}
        for key, (env, _, default, attribute) in KEYS.items():
            value, source = default, "default"
            for name, layer in layers:
                if key in layer:
                    value, source = parse_value(key, layer[key]), ("env:" + env if name == "env" else name)
            setattr(self, attribute, value)
            self.sources[key] = source
        # Provider-dependent defaults, unchanged: a DashScope key selects Qwen.
        if self.provider is None:
            self.provider = "qwen" if os.environ.get("DASHSCOPE_API_KEY") else "openai-compatible"
        if self.model is None:
            self.model = QWEN_MODEL if self.provider == "qwen" else ""
        if self.base_url is None:
            self.base_url = QWEN_BASE_URL if self.provider == "qwen" else ""
        self.api_key, self.api_key_source = "", ""
        for source in ("FTTH_API_KEY", "DASHSCOPE_API_KEY"):
            if os.environ.get(source) and (source == "FTTH_API_KEY" or self.provider == "qwen"):
                self.api_key, self.api_key_source = os.environ[source], "env:" + source
                break
        else:
            stored = credentials.get("model")
            if stored:
                self.api_key, self.api_key_source = stored, "keychain"
        self._runtime, self._runtime_error = None, None
        self._runtime_lock = threading.Lock()
        self.runtime_report = []

    @property
    def model_ready(self):
        return bool(self.model and self.base_url and (self.api_key or self.provider != "qwen"))

    def effective(self):
        """Effective non-secret settings and where each value came from."""
        values = {key: getattr(self, attribute) for key, (_, _, _, attribute) in KEYS.items()}
        return {key: {"value": str(value) if isinstance(value, Path) else value, "source": self.sources[key]}
                for key, value in values.items()}

    def runtime(self, refresh=False):
        """The verified QGIS runtime, resolved once; a failure is retried after a minute."""
        with self._runtime_lock:
            if self._runtime is not None and not refresh:
                return self._runtime
            if self._runtime_error and not refresh and time.monotonic() - self._runtime_error[0] < 60:
                raise QgisUnavailable(self._runtime_error[1])
            report = []
            try:
                self._runtime = resolve(self.qgis_python_config, self.qgis_root_config, self.data, report)
                self._runtime_error = None
            except QgisUnavailable as exc:
                self._runtime_error = (time.monotonic(), str(exc))
                raise
            finally:
                self.runtime_report = report
            return self._runtime

    def use_runtime(self, runtime):
        with self._runtime_lock:
            self._runtime, self._runtime_error = runtime, None

    @property
    def qgis_python(self):
        return self.runtime().python

    def runtime_status(self):
        try:
            return {"available": True, **self.runtime().describe()}
        except QgisUnavailable as exc:
            return {"available": False, "error": str(exc)}

    def worker_env(self):
        extra = worker_packages(self.data)
        return self.runtime().env(os.environ, [extra] if extra.is_dir() else [])
