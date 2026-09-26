"""Where the plugin finds its local backend: the user's saved choice first,
then the defaults `ftth plugin install` wrote next to the plugin."""
import json
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8765"
INSTALLED = Path(__file__).with_name("connection.json")


def installed_defaults(path=INSTALLED):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: data[k] for k in ("url", "token_path") if isinstance(data, dict) and isinstance(data.get(k), str)}


def resolve(saved_url=None, saved_token_path=None, path=INSTALLED):
    """Return (url, token_path or None); a missing token file is skipped."""
    installed = installed_defaults(path)
    url = (saved_url or "").strip() or installed.get("url") or DEFAULT_URL
    token_path = next((p for p in (saved_token_path, installed.get("token_path")) if p and Path(p).is_file()), None)
    return url, token_path


def read_token(token_path):
    try:
        return Path(token_path).read_text(encoding="utf-8").strip() if token_path else ""
    except OSError:
        return ""


def backend_problem(qgis):
    """One status line when the backend cannot run algorithms; older backends omit `qgis`."""
    if not qgis:
        return ""
    if not qgis.get("available"):
        return "后端未找到可用的 QGIS，无法登记数据或运行算法。请在后端终端运行 `uv run ftth doctor` 查看。"
    missing = sorted(set(qgis.get("missing") or []) - {"qgis", "osgeo"})
    if missing:
        return "后端的 QGIS Python 缺少 " + "、".join(missing) + "，完整规划前请运行 `uv run ftth doctor --fix`。"
    return ""
