"""Real source data -> same API used by the plugin -> real algorithm -> export."""
import json
import sys
import time
from pathlib import Path
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import create_app
from backend.settings import Settings
from backend.service import write_json


def main():
    source = ROOT.parent / "FTTH(4)/FTTH/Input/51IMB/Task1/final_data"
    settings = Settings()
    sources = {name: {"path": str(source / (name.lower() + ".csv"))}
               for name in ("IMB", "SITE", "PTECH", "INFRASTRUCTURE")}
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + settings.token
        response = client.post("/projects", json={"name": "51IMB 回归", "sources": sources})
        if response.status_code != 200:
            raise RuntimeError(response.text)
        project = response.json()
        sid = client.post("/sessions", json={"project_id": project["id"]}).json()["id"]
        write_json(settings.data / "smoke_session.json", {"session_id": sid, "project_id": project["id"]})
        payload = {"request_id": "baseline", "scope": "full", "parameter_overrides": {"use_formal_infrastructure": True, "split_oversized_imbs": True}}
        validation = client.post(f"/sessions/{sid}/validate", json=payload)
        print("VALIDATION", validation.text, flush=True)
        response = client.post(f"/sessions/{sid}/runs", json=payload)
        if response.status_code != 202:
            raise RuntimeError(response.text)
        rid = response.json()["id"]
        cursor = 0
        def wait_run(run_id):
            nonlocal cursor
            while True:
                events = client.get(f"/sessions/{sid}/events", params={"after": cursor}).json()
                for event in events:
                    cursor = event["id"]
                    if event["type"] in ("progress", "run"):
                        print(event, flush=True)
                run = client.get(f"/sessions/{sid}/runs/{run_id}").json()
                if run["status"] not in ("queued", "running"):
                    print("RESULT", json.dumps(run, ensure_ascii=False), flush=True)
                    return run
                time.sleep(1)
        run = wait_run(rid)
        write_json(settings.data / "smoke_planning.json", run)
        if run["status"] != "succeeded":
            raise RuntimeError("Planning did not succeed")
        response = client.post(f"/sessions/{sid}/display", json={"run_id": rid})
        print("DISPLAY", response.text, flush=True)
        response = client.post(f"/sessions/{sid}/exports", json={"request_id": "export", "run_id": rid})
        if response.status_code != 202:
            raise RuntimeError(response.text)
        export = wait_run(response.json()["id"])
        write_json(settings.data / "smoke_export.json", export)
        if export["status"] != "succeeded":
            raise RuntimeError("Export did not succeed")


if __name__ == "__main__":
    main()
