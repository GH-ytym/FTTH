"""接入现有 JSON 主程序时使用的适配函数。"""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

from run_task1 import run


def _boolean(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "是"}:
            return True
        if normalized in {"0", "false", "no", "否", ""}:
            return False
    raise ValueError(f"{field} 必须是布尔值")


def build_base_survey_layers(command: Dict[str, Any]) -> Dict[str, Any]:
    """接收完整 command 字典并执行任务一。

    command 可选提供：
    - ``base_dir`` 或 ``_base_dir``：相对路径基准；
    - ``validate_only``：只校验而不调用 QGIS。
    """
    temp_path: Path | None = None
    try:
        if not isinstance(command, dict):
            raise TypeError("command 必须是字典")
        payload = copy.deepcopy(command)
        raw_base_dir = payload.pop("_base_dir", payload.pop("base_dir", None))
        validate_only = _boolean(payload.pop("validate_only", False), "validate_only")
        base_dir = (
            Path(raw_base_dir).expanduser().resolve()
            if raw_base_dir
            else Path.cwd().resolve()
        )
        if not base_dir.exists() or not base_dir.is_dir():
            raise FileNotFoundError(f"任务一 base_dir 不存在：{base_dir}")

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            encoding="utf-8",
            delete=False,
            dir=base_dir,
        ) as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            temp_path = Path(file.name)
        return run(temp_path, validate_only=validate_only)
    except Exception as exc:
        return {
            "status": "failed",
            "operation": "build_base_survey_layers",
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
