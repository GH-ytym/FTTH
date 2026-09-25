import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "ftth"


class Settings:
    def __init__(self, data=None):
        self.data = Path(data or os.environ.get("FTTH_DATA_DIR", ROOT / "runtime")).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        token_file = self.data / "connection.token"
        if not token_file.exists():
            token_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        self.token = token_file.read_text(encoding="utf-8").strip()
        self.qgis = Path(os.environ.get("FTTH_QGIS_ROOT", "D:/osgeo"))
        self.provider = os.environ.get("FTTH_PROVIDER", "qwen" if os.environ.get("DASHSCOPE_API_KEY") else "openai-compatible").strip().lower()
        self.model = os.environ.get("FTTH_MODEL", "qwen3.8-flash" if self.provider == "qwen" else "")
        self.base_url = os.environ.get("FTTH_BASE_URL", "https://maas.qianwenaiapi.com/compatible-mode/v1" if self.provider == "qwen" else "")
        self.api_key = os.environ.get("FTTH_API_KEY", "") or (os.environ.get("DASHSCOPE_API_KEY", "") if self.provider == "qwen" else "")
        self.model_api = os.environ.get("FTTH_MODEL_API", "chat").strip().lower()
        if self.model_api not in {"chat", "responses"}:
            raise ValueError("FTTH_MODEL_API 必须为 chat 或 responses")
        thinking = os.environ.get("FTTH_ENABLE_THINKING", "true").strip().lower()
        if thinking not in {"true", "false", "1", "0"}:
            raise ValueError("FTTH_ENABLE_THINKING 必须为 true 或 false")
        self.enable_thinking = thinking in {"true", "1"}
        self.timeout = int(os.environ.get("FTTH_RUN_TIMEOUT", "3600"))

    @property
    def model_ready(self):
        return bool(self.model and self.base_url and (self.api_key or self.provider != "qwen"))

    def worker_env(self):
        env = os.environ.copy()
        root = self.qgis
        prefix = root / "apps/qgis-ltr"
        if not prefix.exists():
            prefix = root / "apps/qgis"
        env.update(QGIS_PREFIX_PATH=str(prefix), QT_QPA_PLATFORM="offscreen",
                   MPLBACKEND="Agg", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                   PYTHONDONTWRITEBYTECODE="1", PYTHONHOME=str(root / "apps/Python312"),
                   PYTHONPATH=str(prefix / "python"),
                   QT_PLUGIN_PATH=f"{prefix}/qtplugins;{root}/apps/Qt5/plugins")
        env["PATH"] = ";".join(map(str, [root / "bin", prefix / "bin", root / "apps/Qt5/bin"])) + ";" + env.get("PATH", "")
        # Model credentials have no purpose in the algorithm process.
        for key in ("FTTH_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            env.pop(key, None)
        return env
