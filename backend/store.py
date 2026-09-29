import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]


# Update in place so rowid keeps creation order; INSERT OR REPLACE would move an
# updated older record (e.g. a run receiving review progress) after newer ones.
UPSERT = "INSERT INTO objects VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data"
# Must match the objects_session index expression for SQLite to use it.
SESSION = "json_extract(data, '$.session_id')"
# Ordered, append-only schema migrations; PRAGMA user_version counts applied steps.
MIGRATIONS = (
    # 1: tables as of 0.6.0 (earlier databases lack workflow_actions).
    """CREATE TABLE IF NOT EXISTS objects (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id));
    CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, data TEXT);
    CREATE TABLE IF NOT EXISTS requests (session TEXT, key TEXT, value TEXT, PRIMARY KEY(session,key));
    CREATE TABLE IF NOT EXISTS workflow_actions (session TEXT, owner TEXT, action TEXT, digest TEXT, run_id TEXT,
        PRIMARY KEY(session,owner,action));""",
    # 2: session-scoped lookups used by polling endpoints.
    f"""CREATE INDEX IF NOT EXISTS objects_session ON objects(kind, {SESSION});
    CREATE INDEX IF NOT EXISTS events_session ON events(session, id);""",
    # 3: drop cumulative stream snapshots older versions kept. Message objects hold
    # partial and final text; no backend is running during migration, and the
    # pre-migration backup keeps the originals.
    "DELETE FROM events WHERE json_extract(data, '$.type') = 'assistant_stream';",
)
SCHEMA_VERSION = len(MIGRATIONS)


class RequestConflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.path = Path(path)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.migrate()

    def migrate(self):
        current = self.db.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if current == 0 and "workflow_actions" in tables:
            current = 1  # 0.6.0 created these tables before schema versioning.
        if current > SCHEMA_VERSION:
            raise RuntimeError(f"数据库 {self.path} 的结构版本 {current} 高于本程序支持的 {SCHEMA_VERSION}；请使用更新的后端打开，避免旧版本改写数据")
        if current < SCHEMA_VERSION and "objects" in tables:
            # Every upgrade of existing data is preceded by a restorable copy.
            backup = Path(f"{self.path}.pre-schema-v{SCHEMA_VERSION}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.bak")
            dest = sqlite3.connect(backup)
            try:
                self.db.backup(dest)
            finally:
                dest.close()
        for version in range(current, SCHEMA_VERSION):
            self.db.executescript(f"BEGIN;\n{MIGRATIONS[version]}\nPRAGMA user_version = {version + 1};\nCOMMIT;")
        if self.db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.db.commit()

    def put(self, kind, item):
        with self.lock:
            self.db.execute(UPSERT, (kind, item['id'], json.dumps(item, ensure_ascii=False)))
            self.db.commit()
        return item

    def commit_message(self, session, item, key, digest):
        """An accepted turn and its idempotency receipt have one crash boundary."""
        with self.lock, self.db:
            row = self.db.execute('SELECT value FROM requests WHERE session=? AND key=?', (session['id'], key)).fetchone()
            if row:
                previous = self.get('message', row[0])
                if previous.get('request_digest', digest) != digest or previous['content'] != item['content']:
                    raise RequestConflict('请求编号已用于其他消息')
                return previous, False
            item['request_digest'] = digest
            current = self.lookup('session', session['id']) or session
            current['updated'] = session['updated'] if 'updated' in session else time.time()
            if not current.get('title') or current['title'] == '新对话':
                current['title'] = session.get('title', '新对话')
            session = current
            for kind, obj in [('message', item), ('session', session)]:
                self.db.execute(UPSERT, (kind, obj['id'], json.dumps(obj, ensure_ascii=False)))
            self.db.execute('INSERT INTO requests VALUES (?,?,?)', (session['id'], key, item['id']))
            self.db.execute('INSERT INTO events(session,data) VALUES (?,?)', (session['id'], json.dumps(
                dict(type='user', time=time.time(), message_id=item['id'], text=item['content']), ensure_ascii=False)))
            return item, True

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
                if kind == 'workflow':
                    previous = self.lookup(kind, item['id']) or {}
                    item.update(revision=max(previous.get('revision',0),item.get('revision',0))+1,updated=time.time())
                self.db.execute(UPSERT, (kind, item['id'], json.dumps(item, ensure_ascii=False)))
            self.db.execute('INSERT INTO requests VALUES (?,?,?)', (session, request_key, run['id']))
            self.db.execute('INSERT INTO events(session,data) VALUES (?,?)', (session, json.dumps(dict(type='run', time=time.time(), run_id=run['id'], status='queued'))))
            for kind, item in objects:
                if kind == 'workflow':
                    self.db.execute('INSERT INTO events(session,data) VALUES (?,?)', (session, json.dumps(dict(type='workflow', time=time.time(), workflow=item), ensure_ascii=False)))
            return run, True

    def lookup(self, kind, id):
        with self.lock:
            row = self.db.execute("SELECT data FROM objects WHERE kind=? AND id=?", (kind, id)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, kind, id):
        item = self.lookup(kind, id)
        if item is None:
            raise ValueError(f"不存在的 {kind}: {id}")
        return item

    def all(self, kind):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM objects WHERE kind=? ORDER BY rowid", (kind,))]

    def find(self, kind, session_id, /, **where):
        """One session's objects in creation order, filtered by top-level fields in SQL."""
        sql = f"SELECT data FROM objects WHERE kind=? AND {SESSION}=?"
        params = [kind, session_id]
        for field, value in where.items():
            if not field.isidentifier():
                raise ValueError("无效的查询字段：" + field)
            sql += f" AND json_extract(data, '$.{field}')=?"
            params.append(value)
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute(sql + " ORDER BY rowid", params)]

    def event(self, session, kind, **data):
        with self.lock:
            self.db.execute("INSERT INTO events(session,data) VALUES (?,?)", (session, json.dumps(dict(type=kind, time=time.time(), **data), ensure_ascii=False)))
            self.db.commit()

    def publish_message(self, item, events):
        """Commit the message snapshot and its event cursor boundary together."""
        with self.lock, self.db:
            self.db.execute(UPSERT, ("message", item["id"], json.dumps(item, ensure_ascii=False)))
            for kind, data in events:
                self.db.execute("INSERT INTO events(session,data) VALUES (?,?)",
                    (item["session_id"], json.dumps(dict(type=kind, time=time.time(),
                        message_id=item["id"], **({k:item[k] for k in ("run_id","review_id","workflow_id") if k in item} | data)), ensure_ascii=False)))
            if any(kind == "assistant" for kind, _ in events):
                # Each stream event holds the cumulative text, so keeping them
                # grows quadratically with reply length. The final reply event
                # committed here supersedes every snapshot of this message.
                self.db.execute("DELETE FROM events WHERE session=? AND json_extract(data, '$.type')='assistant_stream'"
                    " AND json_extract(data, '$.message_id')=?", (item["session_id"], item["id"]))

    def publish_objects(self, objects, session_id, event_type, **event):
        """One durable boundary for a workflow transition and its UI event."""
        with self.lock, self.db:
            for kind, item in objects:
                self.db.execute(UPSERT, (kind, item['id'], json.dumps(item, ensure_ascii=False)))
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
