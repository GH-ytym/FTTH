import asyncio
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.settings import Settings, VENDOR
from backend.service import Service
from backend.store import Store


async def main():
    with tempfile.TemporaryDirectory(prefix="ftth-env-check-") as directory:
        settings = Settings(directory)
        store = Store(settings.data / "check.sqlite")
        service = Service(settings, store)
        try:
            code, output = await service.process(["-c", "from qgis.core import Qgis; import matplotlib,openpyxl; print(Qgis.QGIS_VERSION)"], settings.data / "environment.log")
            print(output)
            print("Model configured in this terminal:", settings.model_ready)
        finally:
            await service.close()
            store.db.close()
    if code:
        raise SystemExit(code)
    sys.path.insert(0, str(VENDOR))
    from task_bridge import verify_frozen_algorithms
    print(verify_frozen_algorithms(VENDOR))


if __name__ == "__main__":
    asyncio.run(main())
