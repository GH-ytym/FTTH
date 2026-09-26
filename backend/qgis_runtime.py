"""Locate, verify and prepare the QGIS Python used by algorithm workers.

Resolution order: explicit interpreter (``qgis.python``), explicit install root
(``qgis.root``), then discovery. Discovery never guesses from a single
hard-coded path; it asks the system in this order:

1. QGIS executables on PATH (``qgis_process``, ``qgis``, OSGeo4W launchers);
2. the active conda environment and every environment conda has recorded;
3. each platform's documented install locations (OSGeo4W roots and
   Program Files on Windows, application bundles on macOS, the distribution
   package on Linux).

Every candidate is started once to prove that ``qgis.core`` and the worker
modules import. The backend never modifies the QGIS installation: missing
pure-Python modules go to an application-owned directory on ``PYTHONPATH``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("ftth.qgis")

# Third-party modules the frozen algorithms import (qgis and osgeo come with QGIS).
WORKER_MODULES = ("qgis.core", "osgeo.ogr", "matplotlib", "openpyxl")
# Pure-Python requirements the backend may install without touching QGIS.
INSTALLABLE = {"openpyxl": "openpyxl>=3.1,<4"}
PATH_EXECUTABLES = ("qgis_process", "qgis_process-qgis-ltr", "qgis_process-qgis", "qgis-ltr-bin", "qgis-bin", "qgis")
PROBE = """
import importlib, json, sys
result = {"python_version": sys.version.split()[0], "missing": [], "errors": {}}
for name in %r:
    try:
        module = importlib.import_module(name)
        if name == "qgis.core":
            result["qgis_version"] = module.Qgis.QGIS_VERSION
    except Exception as exc:
        result["missing"].append(name.split(".")[0])
        result["errors"][name] = type(exc).__name__ + ": " + str(exc)[:300]
print("FTTH_PROBE=" + json.dumps(result))
""" % (WORKER_MODULES,)
# Model credentials have no purpose in any QGIS subprocess.
SECRET_ENV = ("FTTH_API_KEY", "FTTH_REVIEW_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
COMMON_ENV = {"QT_QPA_PLATFORM": "offscreen", "MPLBACKEND": "Agg", "PYTHONUTF8": "1",
              "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
LAYOUT_LABELS = {"osgeo4w": "OSGeo4W / QGIS 安装包", "macos-bundle": "macOS 应用包", "conda": "conda 环境",
                 "system": "系统 Python", "host": "当前 Python（测试）"}


class QgisUnavailable(ValueError):
    """No usable QGIS; the message tells the user how to fix it."""


@dataclass
class QgisRuntime:
    python: Path
    layout: str
    root: Path
    source: str
    qgis_version: str | None = None
    python_version: str | None = None
    missing: list[str] = field(default_factory=list)
    errors: dict = field(default_factory=dict)

    @classmethod
    def host(cls):
        """The current interpreter; only for process-control tests."""
        return cls(Path(sys.executable), "host", Path(sys.prefix), "test")

    @property
    def usable(self):
        return self.qgis_version is not None and not ({"qgis", "osgeo"} & set(self.missing))

    def env(self, base, extra_paths=()):
        env = {k: v for k, v in base.items() if k not in SECRET_ENV}
        env.update(COMMON_ENV)
        paths = [str(p) for p in extra_paths]
        if self.layout == "host":
            pass
        elif self.layout == "osgeo4w":
            prefix = next((p for p in (self.root / "apps/qgis-ltr", self.root / "apps/qgis") if p.is_dir()), self.root / "apps/qgis")
            qt = next((p for p in (self.root / "apps/Qt5", self.root / "apps/Qt6") if p.is_dir()), self.root / "apps/Qt5")
            env.update(QGIS_PREFIX_PATH=str(prefix), PYTHONHOME=str(osgeo4w_python_home(self.root)),
                       QT_PLUGIN_PATH=os.pathsep.join([str(prefix / "qtplugins"), str(qt / "plugins")]))
            paths.insert(0, str(prefix / "python"))
            env["PATH"] = os.pathsep.join([str(self.root / "bin"), str(prefix / "bin"), str(qt / "bin"), env.get("PATH", "")])
        else:
            # Bundles, conda and distribution Pythons locate their own home.
            env.pop("PYTHONHOME", None)
            if self.layout == "conda":
                env["CONDA_PREFIX"] = str(self.root)
                library = self.root / "Library" if os.name == "nt" else self.root
                env["QGIS_PREFIX_PATH"] = str(library)
                bins = [self.root, self.root / "Library/bin", self.root / "Scripts"] if os.name == "nt" else [self.root / "bin"]
                env["PATH"] = os.pathsep.join([*map(str, bins), env.get("PATH", "")])
            elif self.layout == "system":
                env.setdefault("QGIS_PREFIX_PATH", "/usr")
        if self.layout != "host":
            # Deterministic imports: QGIS's own paths plus app-managed packages only.
            if paths:
                env["PYTHONPATH"] = os.pathsep.join(paths)
            else:
                env.pop("PYTHONPATH", None)
        return env

    def describe(self):
        data = asdict(self)
        data.update(python=str(self.python), root=str(self.root), usable=self.usable,
                    layout_label=LAYOUT_LABELS.get(self.layout, self.layout))
        return data


def osgeo4w_python_home(root):
    homes = sorted((root / "apps").glob("Python3*"), key=lambda p: [int(x) for x in re.findall(r"\d+", p.name)] or [0])
    return homes[-1] if homes else root / "apps/Python312"


def _is_bundle(path):
    return path.suffix == ".app" or (path / "Contents/MacOS").is_dir()


def _is_conda(path):
    return (path / "conda-meta").is_dir()


def _conda_has_qgis(path):
    return any((path / "conda-meta").glob("qgis-[0-9]*.json"))


def runtime_for_root(root, source):
    """Infer the layout of an install root; None if it is not a QGIS install."""
    root = Path(root)
    if _is_bundle(root):
        macos = root / "Contents/MacOS"
        python = next((p for p in (macos / "python", macos / "bin/python3") if p.is_file()), None)
        return QgisRuntime(python, "macos-bundle", root, source) if python else None
    if _is_conda(root):
        python = root / ("python.exe" if os.name == "nt" else "bin/python")
        return QgisRuntime(python, "conda", root, source) if python.is_file() else None
    if (root / "apps").is_dir() and (root / "bin").is_dir():
        python = root / "bin/python.exe"
        if not python.is_file():
            python = osgeo4w_python_home(root) / "python.exe"
        return QgisRuntime(python, "osgeo4w", root, source) if python.is_file() else None
    return None


def runtime_for_python(python, source):
    """Infer the layout that owns an explicitly configured interpreter."""
    python = Path(python)
    for parent in python.parents:
        if parent.suffix == ".app" or _is_conda(parent) or ((parent / "apps").is_dir() and (parent / "bin").is_dir()):
            runtime = runtime_for_root(parent, source)
            if runtime:
                runtime.python = python
                return runtime
    root = python.parent.parent if python.parent.name == "bin" else python.parent
    return QgisRuntime(python, "system", root, source)


def _root_from_executable(executable):
    executable = Path(executable).resolve()
    for parent in executable.parents:
        if parent.suffix == ".app" or _is_conda(parent) or ((parent / "apps").is_dir() and (parent / "bin").is_dir()):
            return parent
    return None


def _standard_roots():
    if os.name == "nt":
        roots = []
        program_files = {os.environ.get(k) for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")} - {None}
        for base in sorted(program_files):
            roots += sorted(Path(base).glob("QGIS*"), reverse=True)
        for drive in (f"{letter}:\\" for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ"):
            if os.path.exists(drive):
                roots += sorted(Path(drive).glob("OSGeo*"))
        return roots
    if sys.platform == "darwin":
        return [p for base in (Path("/Applications"), Path.home() / "Applications")
                for p in sorted(base.glob("QGIS*.app"), key=lambda p: ("LTR" not in p.name, p.name))]
    return []


def _conda_envs():
    envs = []
    if os.environ.get("CONDA_PREFIX"):
        envs.append(Path(os.environ["CONDA_PREFIX"]))
    registry = Path.home() / ".conda/environments.txt"
    if registry.is_file():
        envs += [Path(line.strip()) for line in registry.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    return [env for env in envs if env.is_dir() and _conda_has_qgis(env)]


def candidates():
    """Cheap, ordered filesystem checks. Probing happens separately."""
    found = []
    for name in PATH_EXECUTABLES:
        executable = shutil.which(name)
        if executable:
            root = _root_from_executable(executable)
            if root:
                found.append(runtime_for_root(root, "path:" + name))
            elif Path(executable).resolve().parent == Path("/usr/bin"):
                found.append(QgisRuntime(Path("/usr/bin/python3"), "system", Path("/usr"), "path:" + name))
    found += [runtime_for_root(env, "conda") for env in _conda_envs()]
    found += [runtime_for_root(root, "standard-location") for root in _standard_roots()]
    if sys.platform.startswith("linux") and Path("/usr/bin/python3").is_file() and (
            Path("/usr/share/qgis").is_dir() or Path("/usr/lib/python3/dist-packages/qgis").is_dir()):
        found.append(QgisRuntime(Path("/usr/bin/python3"), "system", Path("/usr"), "standard-location"))
    unique, seen = [], set()
    for runtime in found:
        if runtime and runtime.python.is_file() and str(runtime.python) not in seen:
            seen.add(str(runtime.python))
            unique.append(runtime)
    return unique


def probe(runtime, extra_paths=(), timeout=120):
    """Start the interpreter once; record versions and missing worker modules."""
    runtime.qgis_version, runtime.missing, runtime.errors = None, [], {}
    try:
        output = subprocess.run([str(runtime.python), "-c", PROBE], env=runtime.env(os.environ, extra_paths),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
    except (OSError, subprocess.TimeoutExpired) as exc:
        runtime.missing, runtime.errors = ["qgis"], {"launch": type(exc).__name__ + ": " + str(exc)[:300]}
        return runtime
    line = next((l for l in reversed(output.stdout.splitlines()) if l.startswith("FTTH_PROBE=")), None)
    if not line:
        tail = (output.stderr or output.stdout).strip().splitlines()[-3:]
        runtime.missing, runtime.errors = ["qgis"], {"launch": f"exit {output.returncode}: " + " | ".join(tail)[:300]}
        return runtime
    data = json.loads(line[len("FTTH_PROBE="):])
    runtime.qgis_version, runtime.python_version = data.get("qgis_version"), data.get("python_version")
    runtime.missing, runtime.errors = data["missing"], data["errors"]
    return runtime


def worker_packages(data_dir):
    """App-owned, pure-Python additions for the worker; QGIS stays untouched."""
    return Path(data_dir) / "worker-packages"


def install_missing(runtime, data_dir):
    """Install missing pure-Python worker modules into the app-owned directory."""
    wanted = [INSTALLABLE[m] for m in runtime.missing if m in INSTALLABLE]
    if not wanted:
        return []
    target = worker_packages(data_dir)
    target.mkdir(parents=True, exist_ok=True)
    attempts = [([str(runtime.python), "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
                  "--upgrade", "--target", str(target), *wanted], runtime.env(os.environ))]
    uv = shutil.which("uv")
    if uv and runtime.python_version:
        # Pure-Python wheels only, so the backend's uv can serve a QGIS without pip.
        attempts.append(([uv, "pip", "install", "--target", str(target), "--python-version",
                          ".".join(runtime.python_version.split(".")[:2]), "--only-binary", ":all:", *wanted],
                         {k: v for k, v in os.environ.items() if k not in SECRET_ENV}))
    failures = []
    for command, env in attempts:
        result = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode == 0:
            log.info("installed worker packages %s into %s", wanted, target)
            return wanted
        failures.append(((result.stderr or result.stdout).strip().splitlines() or ["unknown error"])[-1])
    raise RuntimeError("无法安装 " + "、".join(wanted) + "：" + "；".join(failures))


def remediation():
    return ("请安装 QGIS 3（Windows: OSGeo4W 或 QGIS 安装包；macOS: QGIS.app；Linux: 发行版 qgis 包或 conda-forge qgis），"
            "或用 `ftth config set qgis.python <QGIS 的 Python 解释器>` / `qgis.root <安装目录>` 指定；"
            "运行 `ftth doctor` 查看检测过程。")


def resolve(explicit_python=None, explicit_root=None, data_dir=None, report=None):
    """Return the first usable runtime; ``report`` collects every attempt."""
    report = report if report is not None else []
    if explicit_python:
        options = [runtime_for_python(explicit_python, "config:qgis.python")]
    elif explicit_root:
        runtime = runtime_for_root(explicit_root, "config:qgis.root")
        if runtime is None:
            raise QgisUnavailable(f"qgis.root={explicit_root} 不是可识别的 QGIS 安装目录（OSGeo4W、QGIS.app 或 conda 环境）。" + remediation())
        options = [runtime]
    else:
        options = candidates()
    for runtime in options:
        if not runtime.python.is_file():
            runtime.errors = {"launch": "解释器不存在"}
            report.append(runtime)
            continue
        probe(runtime, [worker_packages(data_dir)] if data_dir else [])
        report.append(runtime)
        if runtime.usable:
            log.info("QGIS %s (%s, %s): %s", runtime.qgis_version, runtime.layout, runtime.source, runtime.python)
            return runtime
        log.warning("QGIS candidate rejected (%s): %s %s", runtime.source, runtime.python, runtime.errors)
    if explicit_python or explicit_root:
        detail = report[-1].errors if report else {}
        raise QgisUnavailable(f"配置的 QGIS 不可用：{explicit_python or explicit_root}（{json.dumps(detail, ensure_ascii=False)[:300]}）。" + remediation())
    raise QgisUnavailable("未找到可用的 QGIS。" + remediation())
