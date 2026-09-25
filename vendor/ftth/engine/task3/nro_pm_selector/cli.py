"""命令行入口：python -m nro_pm_selector --input ... --output ..."""

from __future__ import annotations

import argparse
import json
import sys

from .io import InputBundle, write_outputs
from .selector import SelectionEngine


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="第三阶段：NRO 核心机房和 PM 光交箱自动选址")
    result.add_argument("--input", required=True, help="输入图层目录（CSV/GeoJSON）")
    result.add_argument("--output", required=True, help="输出目录")
    result.add_argument("--config", help="可选配置 JSON；默认读取输入目录/config.json")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        bundle = InputBundle.load(args.input, args.config)
        result = SelectionEngine(bundle).run()
        write_outputs(args.output, bundle, result.selections, result.rejections, result.validation)
        print(json.dumps({
            "ok": result.validation["passed"],
            "output": args.output,
            "validation": result.validation,
            "warnings": bundle.warnings,
        }, ensure_ascii=False, indent=2))
        return 0 if result.validation["passed"] else 2
    except Exception as exc:  # CLI 边界统一给出可读错误；库调用仍可捕获具体异常。
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
