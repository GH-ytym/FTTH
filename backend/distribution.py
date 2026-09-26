"""Plugin packaging and installation into a QGIS profile, on every platform."""
import configparser
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

from .settings import ROOT

PLUGIN = "ftth_demo0"
SOURCE = ROOT / "qgis_plugin" / PLUGIN


# connection.json is machine-specific: written at install time, never shipped.
LOCAL_FILES = {".lock", "connection.json"}


def shipped(path):
    return path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".whl") and path.name not in LOCAL_FILES


def plugin_version():
    metadata = configparser.ConfigParser()
    metadata.read(SOURCE / "metadata.txt", encoding="utf-8")
    return metadata["general"]["version"]


def package(dist=ROOT / "dist"):
    """Build dist/ftth_demo0.zip, a versioned copy and a release manifest."""
    dist.mkdir(parents=True, exist_ok=True)
    output = dist / f"{PLUGIN}.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(SOURCE.rglob("*")):
            if shipped(path):
                archive.write(path, path.relative_to(SOURCE.parent))
    version = plugin_version()
    versioned = output.with_name(f"{PLUGIN}-{version}.zip")
    shutil.copy2(output, versioned)
    manifest = {"version": version, "archive": versioned.name,
                "sha256": hashlib.sha256(versioned.read_bytes()).hexdigest(),
                "requires_backend": version, "files": {}}
    for prefix, base in (("", ROOT / "backend"), ("qgis_plugin/", SOURCE)):
        for path in sorted(base.rglob("*")):
            if shipped(path):
                key = prefix + path.relative_to(base.parent).as_posix()
                manifest["files"][key] = hashlib.sha256(path.read_bytes()).hexdigest()
    release = dist / f"release-{version}.json"
    release.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output, versioned, release


def profiles_dir():
    """QGIS 3 user profiles, per the platform's application-data convention."""
    if os.name == "nt":
        base = Path(os.environ["APPDATA"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "QGIS/QGIS3/profiles"


def install(profile="default", update=False, profiles=None, backups=None, connection=None):
    """Copy the plugin into a profile; an existing copy is backed up, then replaced.

    ``connection`` ({"url", "token_path"}) lets the installed plugin reach this
    machine's backend without the user locating the token file."""
    if not re.fullmatch(r"[\w-]+", profile):
        raise ValueError("QGIS 配置名只能包含字母、数字、下划线和连字符")
    profiles = Path(profiles) if profiles else profiles_dir()
    target = profiles / profile / "python/plugins" / PLUGIN
    backup = None
    if target.exists():
        if not update:
            raise ValueError(f"目标已存在：{target}。使用 --update 先备份再更新。")
        backup = Path(backups or ROOT / "runtime/plugin-backups") / f"{PLUGIN}-{time.strftime('%Y%m%d-%H%M%S')}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target, backup)
        # Replace rather than overlay, so files removed upstream do not linger.
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE, target, ignore=_unshipped)
    if connection:
        (target / "connection.json").write_text(json.dumps(connection, ensure_ascii=False, indent=2), encoding="utf-8")
    return target, backup


def _unshipped(folder, names):
    return [n for n in names if n == "__pycache__" or ((Path(folder) / n).is_file() and not shipped(Path(folder) / n))]


def ui_test_env(directory, argv=()):
    """Environment for plugin UI verification: throwaway preferences, and with
    ``--installed [--profiles-dir DIR]`` the installed copy instead of the source."""
    env = {"FTTH_PLUGIN_SETTINGS": str(Path(directory) / "plugin-settings.ini")}
    if "--installed" in argv:
        profiles = Path(argv[argv.index("--profiles-dir") + 1]) if "--profiles-dir" in argv else profiles_dir()
        env["DEMO_TEST_PLUGINS"] = str(profiles / "default/python/plugins")
    return env
