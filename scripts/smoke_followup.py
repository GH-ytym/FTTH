"""Run a changed plan against the running local service; save comparison."""
import json
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
runtime = ROOT / "runtime"
run = json.loads((runtime / "smoke_planning.json").read_text(encoding="utf-8"))
sid = run["session_id"]
with httpx.Client(base_url="http://127.0.0.1:8765", headers={"Authorization": "Bearer " + (runtime / "connection.token").read_text().strip()}, timeout=30) as client:
    response = client.post(f"/sessions/{sid}/runs", json={"request_id": "smoke-followup-600", "base_run_id": run["id"], "parameter_overrides": {"zpm_max_cap": 600, "ga_generations": 4}})
    response.raise_for_status()
    rid = response.json()["id"]
    while True:
        current = client.get(f"/sessions/{sid}/runs/{rid}").json()
        if current["status"] not in ("queued", "running"):
            break
        time.sleep(1)
    (runtime / "smoke_followup.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    print(current["id"], current["status"], flush=True)
    assert current["status"] == "succeeded", current.get("result")
    response = client.get(f"/sessions/{sid}/compare", params={"left": run["id"], "right": rid})
    response.raise_for_status()
    (runtime / "comparison.json").write_text(json.dumps(response.json(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(response.text)
