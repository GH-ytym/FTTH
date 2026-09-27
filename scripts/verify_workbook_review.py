"""Audit one existing run read-only; never rewrites history or executes planning."""
import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.start_backend import read_env
from backend.settings import Settings
from backend.service import Service
from backend.review import Evidence, ReviewController
from backend.review_chain import prepare_checks

class ReadStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, check_same_thread=False)
    def get(self, kind, identifier):
        row = self.db.execute('SELECT data FROM objects WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        if row is None:
            raise ValueError('record not found')
        return json.loads(row[0])

class Reader:
    process = Service.process
    kill = Service.kill
    verify_snapshot = Service.verify_snapshot

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_id')
    parser.add_argument('--real-model', action='store_true')
    args = parser.parse_args()
    if args.real_model:
        for k,v in read_env(ROOT / '.env').items():
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
    reader = Reader(); reader.settings = Settings(); reader.store = ReadStore(reader.settings.data / 'app.sqlite')
    run = reader.store.get('run', args.run_id)
    evidence = Evidence(reader, run)
    directory = reader.settings.data / 'audit' / ('workbook_' + args.run_id)
    directory.mkdir(parents=True, exist_ok=True)
    report = await prepare_checks(reader, evidence, directory)
    evidence.verify()
    print(json.dumps({'counts':report['counts'], 'truncated':evidence.truncated},ensure_ascii=False),flush=True)
    if args.real_model:
        result = await ReviewController(reader).infer(evidence)
        if result is None:
            raise ValueError('model not configured')
        (directory / 'model_review.json').write_text(result.model_dump_json(indent=2),encoding='utf-8')
        print(json.dumps({'verdict':result.verdict,'findings':len(result.findings),'checked':result.checked},ensure_ascii=False),flush=True)
    reader.store.db.close()

if __name__ == '__main__':
    asyncio.run(main())
