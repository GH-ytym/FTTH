"""任务一统一入口：普通 Python 校验，QGIS 环境完整建图。"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict

from task1_core import (
    Task1Error,
    build_quality_report,
    load_config,
    resolve_path,
    save_report,
)

TASK1_VERSION = "1.7.0"


def _failed_report_path(report_path: Path) -> Path:
    suffix = report_path.suffix or ".json"
    return report_path.with_name(f"{report_path.stem}.failed{suffix}")


def _qgis_available() -> bool:
    try:
        import qgis.core  # noqa: F401
    except ImportError:
        return False
    return True


def run(config_path: str | Path, validate_only: bool = False) -> Dict[str, Any]:
    print(f"[INFO] Task1 version {TASK1_VERSION}")
    config, base_dir = load_config(config_path)
    report = build_quality_report(config, base_dir)

    output_cfg = config.get("output", {})
    report_path = resolve_path(
        base_dir,
        output_cfg.get("quality_report", "outputs/data_quality_report.json"),
    )
    print(json.dumps(report["summary"], ensure_ascii=False))

    if report["status"] == "error":
        failed_path = _failed_report_path(report_path)
        save_report(report, failed_path)
        raise Task1Error(f"输入数据存在错误，已停止建图；失败报告：{failed_path}")

    if validate_only:
        save_report(report, report_path)
        print(f"[OK] 数据质量报告：{report_path}")
        print("[OK] validate-only 模式完成，未调用 QGIS。")
        return {
            "status": "success",
            "version": TASK1_VERSION,
            "quality_report": str(report_path),
            "report": report,
        }

    if not _qgis_available():
        raise Task1Error(
            "当前 Python 环境没有 qgis 模块。请使用 --validate-only，"
            "或在 QGIS Python 控制台中运行。"
        )

    from task1_qgis import auto_set_style, build_qgis_layers, capture_canvas_img

    try:
        qgis_result = build_qgis_layers(config, base_dir)
    except Exception as exc:
        failed_report = copy.deepcopy(report)
        failed_report["status"] = "error"
        failed_report["runtime_errors"] = [f"{type(exc).__name__}: {exc}"]
        failed_report["summary"]["error_count"] = int(
            failed_report["summary"].get("error_count", 0)
        ) + 1
        failed_path = _failed_report_path(report_path)
        save_report(failed_report, failed_path)
        raise Task1Error(f"QGIS 建图失败；旧成果未覆盖；失败报告：{failed_path}") from exc
    layer_dict = qgis_result.pop("_layer_dict", {})
    runtime_warnings: list[str] = list(qgis_result.pop("runtime_warnings", []))

    if output_cfg.get("apply_style", True):
        print("[VIS] 正在配置图层样式和标注...")
        try:
            auto_set_style(layer_dict, config["vector_layers"])
        except Exception as exc:
            runtime_warnings.append(
                f"核心成果已生成，但图层样式设置失败：{type(exc).__name__}: {exc}"
            )

    if output_cfg.get("capture_screenshot", True):
        if not output_cfg.get("add_to_project", True):
            runtime_warnings.append("add_to_project=false，已跳过工程全景截图")
        else:
            screenshot_path = resolve_path(
                base_dir,
                output_cfg.get("screenshot", "outputs/工程可视化全景.png"),
            )
            try:
                capture_canvas_img(screenshot_path)
                qgis_result["screenshot"] = str(screenshot_path)
                print(f"[VIS] 全景截图已保存：{screenshot_path}")
            except Exception as exc:  # 截图失败不应破坏核心图层成果
                runtime_warnings.append(
                    f"工程图层已生成，但截图失败：{type(exc).__name__}: {exc}"
                )

    save_report(report, report_path)
    print(f"[OK] 数据质量报告：{report_path}")

    result: Dict[str, Any] = {
        "status": "success",
        "version": TASK1_VERSION,
        "quality_report": str(report_path),
        "report": report,
        **qgis_result,
        "runtime_warnings": runtime_warnings,
    }

    print("[OK] 任务一执行完成")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "report"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="任务一：基础数据与场勘图层构建")
    parser.add_argument("config", nargs="?", default="task1_config.json")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        run(args.config, validate_only=args.validate_only)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
