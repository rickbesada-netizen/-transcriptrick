"""SQLite storage for batches, jobs and summaries."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches(
  id TEXT PRIMARY KEY, title TEXT, url TEXT, source TEXT, is_collection INTEGER DEFAULT 0, created REAL
);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, batch_id TEXT REFERENCES batches(id) ON DELETE CASCADE, position INTEGER DEFAULT 0,
  source TEXT, kind TEXT, url TEXT, title TEXT, author TEXT, duration REAL, thumbnail TEXT, meta TEXT DEFAULT '{}',
  status TEXT DEFAULT 'queued', stage TEXT DEFAULT 'Waiting', progress REAL DEFAULT 0, error TEXT,
  method TEXT, language TEXT, segments TEXT, text TEXT, created REAL, finished REAL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_batch ON jobs(batch_id);
CREATE TABLE IF NOT EXISTS downloads(
  id TEXT PRIMARY KEY, group_id TEXT, group_title TEXT, position INTEGER DEFAULT 0, url TEXT, source TEXT,
  title TEXT, author TEXT, duration REAL, thumbnail TEXT, format TEXT, quality TEXT,
  status TEXT DEFAULT 'queued', stage TEXT DEFAULT 'Waiting', progress REAL DEFAULT 0, error TEXT,
  file TEXT, size INTEGER, created REAL, finished REAL
);
CREATE INDEX IF NOT EXISTS downloads_status ON downloads(status);
CREATE TABLE IF NOT EXISTS summaries(
  owner TEXT, style TEXT, status TEXT DEFAULT 'pending', content TEXT, error TEXT, model TEXT, updated REAL,
  PRIMARY KEY(owner, style)
);
"""

_write = threading.Lock()


@contextmanager
def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init() -> None:
    with _write, connect() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        # Work interrupted by closing the app goes back in the queue.
        con.execute("UPDATE jobs SET status='queued', stage='Waiting', progress=0 WHERE status='working'")
        con.execute("UPDATE downloads SET status='queued', stage='Waiting', progress=0 WHERE status='working'")
        con.execute("UPDATE summaries SET status='failed', error='Interrupted. Generate it again.' "
                    "WHERE status IN ('pending','working')")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def create_batch(title, url, source, is_collection, items) -> str:
    batch_id, now = new_id(), time.time()
    with _write, connect() as con:
        con.execute("INSERT INTO batches VALUES(?,?,?,?,?,?)",
                    (batch_id, title, url, source, int(is_collection), now))
        for i, it in enumerate(items):
            con.execute(
                "INSERT INTO jobs(id,batch_id,position,source,kind,url,title,author,duration,thumbnail,meta,created)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id(), batch_id, i, it.get("source"), it.get("kind"), it.get("url"), it.get("title"),
                 it.get("author"), it.get("duration"), it.get("thumbnail"), json.dumps(it.get("meta") or {}),
                 now + i / 1000))
    return batch_id


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with _write, connect() as con:
        con.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def next_queued() -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
    return _job(row) if row else None


def _job(row, full=True) -> dict:
    d = dict(row)
    d["meta"] = json.loads(d.get("meta") or "{}")
    if full:
        d["segments"] = json.loads(d["segments"]) if d.get("segments") else []
    return d


def get_job(job_id: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _job(row) if row else None


def job_status(job_id: str) -> str | None:
    with connect() as con:
        row = con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    return row["status"] if row else None


LIGHT = ("id,batch_id,position,source,kind,url,title,author,duration,thumbnail,status,stage,progress,error,"
         "method,language,created,finished")


def library() -> list[dict]:
    with connect() as con:
        batches = [dict(b) for b in con.execute("SELECT * FROM batches ORDER BY created DESC")]
        rows = con.execute(f"SELECT {LIGHT} FROM jobs ORDER BY position").fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["batch_id"], []).append(dict(r))
    for b in batches:
        b["jobs"] = grouped.get(b["id"], [])
    return batches


def get_batch(batch_id: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not row:
            return None
        jobs = con.execute("SELECT * FROM jobs WHERE batch_id=? ORDER BY position", (batch_id,)).fetchall()
    batch = dict(row)
    batch["jobs"] = [_job(j) for j in jobs]
    return batch


def delete_batch(batch_id: str) -> None:
    with _write, connect() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM jobs WHERE batch_id=?", (batch_id,))]
        for owner in [batch_id, *ids]:
            con.execute("DELETE FROM summaries WHERE owner=?", (owner,))
        con.execute("DELETE FROM batches WHERE id=?", (batch_id,))


def delete_job(job_id: str) -> None:
    with _write, connect() as con:
        row = con.execute("SELECT batch_id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return
        con.execute("DELETE FROM summaries WHERE owner=?", (job_id,))
        con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        left = con.execute("SELECT COUNT(*) c FROM jobs WHERE batch_id=?", (row["batch_id"],)).fetchone()["c"]
        if not left:
            con.execute("DELETE FROM summaries WHERE owner=?", (row["batch_id"],))
            con.execute("DELETE FROM batches WHERE id=?", (row["batch_id"],))


def search(query: str, limit: int = 40) -> list[dict]:
    like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with connect() as con:
        rows = con.execute(
            "SELECT id,title,author,source,batch_id,text FROM jobs WHERE status='done' AND "
            "(text LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\') ORDER BY created DESC LIMIT ?",
            (like, like, limit)).fetchall()
    out, q = [], query.lower()
    for r in rows:
        text = r["text"] or ""
        at = text.lower().find(q)
        snippet = text[max(0, at - 70): at + 130].strip() if at >= 0 else text[:200]
        out.append({"id": r["id"], "title": r["title"], "author": r["author"], "source": r["source"],
                    "batch_id": r["batch_id"], "snippet": snippet})
    return out


def set_summary(owner: str, style: str, **fields) -> None:
    fields["updated"] = time.time()
    with _write, connect() as con:
        con.execute("INSERT OR IGNORE INTO summaries(owner,style) VALUES(?,?)", (owner, style))
        cols = ", ".join(f"{k}=?" for k in fields)
        con.execute(f"UPDATE summaries SET {cols} WHERE owner=? AND style=?", (*fields.values(), owner, style))


def get_summaries(owner: str) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM summaries WHERE owner=? ORDER BY updated", (owner,))]


def summary_flags() -> dict:
    with connect() as con:
        rows = con.execute("SELECT owner, status FROM summaries").fetchall()
    return {r["owner"]: r["status"] for r in rows}


# ---------- Downloads ----------

DL_FIELDS = ("url", "source", "title", "author", "duration", "thumbnail")


def add_downloads(group_title: str | None, is_collection: bool, items: list[dict], fmt: str, quality: str) -> list[str]:
    group_id, now, ids = (new_id() if is_collection else None), time.time(), []
    with _write, connect() as con:
        for i, it in enumerate(items):
            did = new_id()
            ids.append(did)
            con.execute(
                "INSERT INTO downloads(id,group_id,group_title,position,url,source,title,author,duration,thumbnail,"
                "format,quality,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (did, group_id, group_title if is_collection else None, i, *(it.get(k) for k in DL_FIELDS),
                 fmt, quality, now + i / 1000))
    return ids


def update_download(did: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with _write, connect() as con:
        con.execute(f"UPDATE downloads SET {cols} WHERE id=?", (*fields.values(), did))


def get_download(did: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM downloads WHERE id=?", (did,)).fetchone()
    return dict(row) if row else None


def download_status(did: str) -> str | None:
    with connect() as con:
        row = con.execute("SELECT status FROM downloads WHERE id=?", (did,)).fetchone()
    return row["status"] if row else None


def next_download() -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM downloads WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
    return dict(row) if row else None


def list_downloads() -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM downloads ORDER BY created DESC")]


def delete_download(did: str) -> None:
    with _write, connect() as con:
        con.execute("DELETE FROM downloads WHERE id=?", (did,))


def clear_finished_downloads() -> None:
    with _write, connect() as con:
        con.execute("DELETE FROM downloads WHERE status IN ('done','failed','cancelled')")
