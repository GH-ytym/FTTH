"""Local launcher: read .env as data, then call the existing FTTH CLI."""
import argparse
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_env(path):
    values = {}
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, separator, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if (not separator or not re.fullmatch(r'FTTH_[A-Z0-9_]+|DASHSCOPE_API_KEY', name)
                or name in values):
            raise ValueError(f'.env 第 {number} 行格式错误或配置名重复；请使用 NAME=value')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'.env 第 {number} 行引号未闭合')
            value = value[1:-1]
        values[name] = value
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description='使用本地 .env 启动 FTTH 后端')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--check-config', action='store_true')
    args, extra = parser.parse_known_args(argv)
    os.chdir(ROOT)
    path = ROOT / '.env'
    if not path.exists():
        path.write_bytes((ROOT / '.env.example').read_bytes())
        print(f'已创建 {path}，请填写 FTTH_API_KEY 后重新启动。')
        return 2
    try:
        # File entries override the launching terminal; blank entries clear stale values.
        # No shell evaluation, interpolation or escape processing is performed.
        for name, value in read_env(path).items():
            if value:
                os.environ[name] = value
            else:
                os.environ.pop(name, None)
        from backend.cli import main as cli
        if args.stop:
            return cli(['stop', *extra])
        from backend.settings import Settings
        settings = Settings()
        if not settings.model_ready:
            print(f'模型配置不完整，请在 {path} 填写 FTTH_API_KEY，并核对模型名称和接口地址。')
            return 2
        if args.check_config:
            print('配置已读取，模型参数齐全；未启动后端、未调用模型。')
            return 0
        return cli(['serve', *extra])
    except ValueError as exc:
        # Env parser errors contain line numbers only; settings errors can contain values.
        print(str(exc) if str(exc).startswith('.env 第 ') else '配置无效，请检查 .env；未输出配置值。')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
