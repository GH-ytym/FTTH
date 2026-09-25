from pathlib import Path
import zipfile
import configparser
import hashlib
import json
import shutil

root = Path(__file__).resolve().parents[1]
output = root / "dist/ftth_demo0.zip"
output.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in (root / "qgis_plugin/ftth_demo0").rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".whl") and path.name != ".lock":
            archive.write(path, path.relative_to(root / "qgis_plugin"))
print(output)
metadata = configparser.ConfigParser()
metadata.read(root / "qgis_plugin/ftth_demo0/metadata.txt", encoding="utf-8")
version = metadata["general"]["version"]
versioned = output.with_name(f"ftth_demo0-{version}.zip")
shutil.copy2(output, versioned)
manifest = {"version": version, "archive": versioned.name,
    "sha256": hashlib.sha256(versioned.read_bytes()).hexdigest(),
    "requires_backend": version, "files": {}}
for base in (root / "backend", root / "qgis_plugin/ftth_demo0"):
    for path in base.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".whl") and path.name != ".lock":
            manifest["files"][path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
(root / f"dist/release-{version}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(versioned)
