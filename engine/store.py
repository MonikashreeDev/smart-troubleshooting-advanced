"""SQLite persistence: sessions, feedback events and telemetry history.

Standard-library sqlite3 only. The default file lives under /tmp so the free
Render instance can write it; it is wiped on restart/redeploy (documented).
"""
from __future__ import annotations
import json, os, sqlite3, threading, time, uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS session_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, ts REAL NOT NULL,
  kind TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, session_id TEXT,
  source_id TEXT NOT NULL, catalog_id TEXT, symptom TEXT, outcome TEXT NOT NULL
  CHECK (outcome IN ('fixed','not_fixed')));
CREATE TABLE IF NOT EXISTS telemetry (
  id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, day INTEGER NOT NULL,
  metric TEXT NOT NULL, value REAL NOT NULL, synthetic INTEGER NOT NULL DEFAULT 1,
  UNIQUE(device_id, day, metric));
CREATE INDEX IF NOT EXISTS idx_feedback_source ON feedback(source_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id);
"""

class Store:
    def __init__(self, path=None):
        self.path = path or os.getenv('SGT_DB', '/tmp/sgt_advanced.sqlite3')
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript(SCHEMA); self.db.commit()

    # ---- sessions --------------------------------------------------------
    def new_session(self, state):
        sid = uuid.uuid4().hex[:16]; now = time.time()
        with self.lock:
            self.db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (sid, now, now, json.dumps(state))); self.db.commit()
        return sid
    def get_session(self, sid):
        with self.lock:
            row = self.db.execute('SELECT state FROM sessions WHERE id=?', (sid,)).fetchone()
        return json.loads(row[0]) if row else None
    def save_session(self, sid, state):
        with self.lock:
            self.db.execute('UPDATE sessions SET state=?, updated_at=? WHERE id=?', (json.dumps(state), time.time(), sid)); self.db.commit()
    def log_event(self, sid, kind, payload):
        with self.lock:
            self.db.execute('INSERT INTO session_events (session_id, ts, kind, payload) VALUES (?,?,?,?)',
                            (sid, time.time(), kind, json.dumps(payload, ensure_ascii=False))); self.db.commit()
    def session_events(self, sid):
        with self.lock:
            rows = self.db.execute('SELECT ts, kind, payload FROM session_events WHERE session_id=? ORDER BY id', (sid,)).fetchall()
        return [{'ts': r[0], 'kind': r[1], **json.loads(r[2])} for r in rows]
    def session_count(self):
        with self.lock: return self.db.execute('SELECT count(*) FROM sessions').fetchone()[0]

    # ---- feedback --------------------------------------------------------
    def add_feedback(self, source_id, outcome, session_id=None, catalog_id=None, symptom=None):
        if outcome not in ('fixed', 'not_fixed'): raise ValueError('bad_outcome')
        with self.lock:
            self.db.execute('INSERT INTO feedback (ts, session_id, source_id, catalog_id, symptom, outcome) VALUES (?,?,?,?,?,?)',
                            (time.time(), session_id, source_id, catalog_id, symptom, outcome)); self.db.commit()
    def feedback_counts(self):
        with self.lock:
            rows = self.db.execute("SELECT source_id, sum(outcome='fixed'), sum(outcome='not_fixed') FROM feedback GROUP BY source_id").fetchall()
        return {r[0]: {'fixed': int(r[1] or 0), 'not_fixed': int(r[2] or 0)} for r in rows}

    # ---- telemetry -------------------------------------------------------
    def put_telemetry(self, device_id, day, metric, value, synthetic=True):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO telemetry (device_id, day, metric, value, synthetic) VALUES (?,?,?,?,?)',
                            (device_id, int(day), metric, float(value), 1 if synthetic else 0)); self.db.commit()
    def put_telemetry_many(self, rows):
        with self.lock:
            self.db.executemany('INSERT OR REPLACE INTO telemetry (device_id, day, metric, value, synthetic) VALUES (?,?,?,?,?)', rows); self.db.commit()
    def telemetry(self, device_id):
        with self.lock:
            rows = self.db.execute('SELECT metric, day, value FROM telemetry WHERE device_id=? ORDER BY metric, day', (device_id,)).fetchall()
        out = {}
        for m, d, v in rows: out.setdefault(m, []).append((d, v))
        return out
    def devices(self):
        with self.lock: return [r[0] for r in self.db.execute('SELECT DISTINCT device_id FROM telemetry ORDER BY device_id')]
