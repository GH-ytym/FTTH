import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='objects'").fetchone()
        migrated = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_actions'").fetchone()
        if exists and not migrated:
            backup = Path(str(path) + '.pre-0.6.0-' + uuid.uuid4().hex[:8] + '.bak')
            with sqlite3.connect(backup) as dest:
                self.db.backup(dest)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS objects (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id));
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, data TEXT);
        CREATE TABLE IF NOT EXISTS requests (session TEXT, key TEXT, value TEXT, PRIMARY KEY(session,key));
        CREATE TABLE IF NOT EXISTS workflow_actions (session TEXT, owner TEXT, action TEXT, digest TEXT, run_id TEXT,
            PRIMARY KEY(session,owner,action));
        """)
        self.db.commit()

    def put(self, kind, item):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO objects VALUES (?,?,?)", (kind, item['id'], json.dumps(item, ensure_ascii=False)))
            self.db.commit()
        return item

    def commit_action(self, session, owner, action, digest, run, objects, request_key):
        """Claim and durable business effects share one SQLite transaction."""
        with self.lock, self.db:
            row = self.db.execute('SELECT digest,run_id FROM workflow_actions WHERE session=? AND owner=? AND action=?',
                (session, owner, action)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError('本次请求已提交另一组参数，不能重复创建任务')
                return self.get('run', row[1]), False
            self.db.execute('INSERT INTO workflow_actions VALUES (?,?,?,?,?)', (session, owner, action, digest, run['id']))
            for kind, item in [('run', run), *objects]:
                self.db.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?)', (kind, item['id'], json.dumps(item, ensure_ascii=False)))
            self.db.execute('INSERT INTO requests VALUES (?,?,?)', (session, request_key, run['id']))
            self.db.execute('INSERT INTO events(session,data) VALUES (?,?)', (session, json.dumps(dict(type='run', time=time.time(), run_id=run['id'], status='queued'))))
            for kind, item in objects:
                if kind == 'workflow':
                    self.db.execute('INSERT INTO events(session,data) VALUES (?,?)', (session, json.dumps(dict(type='workflow', time=time.time(), workflow=item), ensure_ascii=False)))
            return run, True

    def get(self, kind, id):
        with self.lock:
            row = self.db.execute("SELECT data FROM objects WHERE kind=? AND id=?", (kind, id)).fetchone()
        if not row:
            raise ValueError(f"不存在的 {kind}: {id}")
        return json.loads(row[0])

    def all(self, kind):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM objects WHERE kind=? ORDER BY rowid", (kind,))]

    def event(self, session, kind, **data):
        with self.lock:
            self.db.execute("INSERT INTO events(session,data) VALUES (?,?)", (session, json.dumps(dict(type=kind, time=time.time(), **data), ensure_ascii=False)))
            self.db.commit()

    def publish_message(self, item, events):
        """Commit the message snapshot and its event cursor boundary together."""
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO objects VALUES (?,?,?)",
                ("message", item["id"], json.dumps(item, ensure_ascii=False)))
            for kind, data in events:
                self.db.execute("INSERT INTO events(session,data) VALUES (?,?)",
                    (item["session_id"], json.dumps(dict(type=kind, time=time.time(),
                        message_id=item["id"], **data), ensure_ascii=False)))

    def publish_objects(self, objects, session_id, event_type, **event):
        """One durable boundary for a workflow transition and its UI event."""
        with self.lock, self.db:
            for kind, item in objects:
                self.db.execute("INSERT OR REPLACE INTO objects VALUES (?,?,?)",
                    (kind, item['id'], json.dumps(item, ensure_ascii=False)))
            self.db.execute("INSERT INTO events(session,data) VALUES (?,?)",
                (session_id, json.dumps(dict(type=event_type, time=time.time(), **event), ensure_ascii=False)))

    def stream_message(self, message_id, stream_id, text):
        with self.lock:
            item = self.get("message", message_id)
            if item["status"] != "running":
                return
            item.update(partial_response=text, stream_id=stream_id)
            self.publish_message(item, [("assistant_stream", {"stream_id": stream_id, "text": text})])

    def events(self, session, after):
        with self.lock:
            return [dict(id=r[0], **json.loads(r[1])) for r in self.db.execute("SELECT id,data FROM events WHERE session=? AND id>? ORDER BY id LIMIT 200", (session, after))]

    def dedup(self, session, key, value=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM requests WHERE session=? AND key=?", (session, key)).fetchone()
            if row:
                return row[0]
            if value:
                self.db.execute("INSERT INTO requests VALUES (?,?,?)", (session, key, value))
                self.db.commit()
            return None
