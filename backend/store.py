import json
import sqlite3
import threading
import time
import uuid


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS objects (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id));
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, data TEXT);
        CREATE TABLE IF NOT EXISTS requests (session TEXT, key TEXT, value TEXT, PRIMARY KEY(session,key));
        """)
        self.db.commit()

    def put(self, kind, item):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO objects VALUES (?,?,?)", (kind, item['id'], json.dumps(item, ensure_ascii=False)))
            self.db.commit()
        return item

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
