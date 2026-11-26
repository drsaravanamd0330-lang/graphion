"""Where articles live: a directory per article, indexed by SQLite.

Filesystem first, the way Graphion works: the manuscript, the extracted and
editor records, and every proof revision are files a successor can read
without this program. SQLite holds the index, approvals, acknowledgements and
journal-level evidence status.

    studio_data/articles/<article-id>/
        source.docx            the manuscript as received (never modified)
        extracted.json         what the importer read
        editor.json            editor-approved metadata
        record.json            the merged record (rebuilt on every change)
        media/                 images extracted from the manuscript
        revisions/r001/        one directory per proof revision, never rewritten
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    journal_id TEXT NOT NULL,
    article_type TEXT NOT NULL,
    title TEXT,
    source_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    input_key TEXT NOT NULL,
    status TEXT NOT NULL,            -- diagnostic | proof | approved | failed
    created_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    summary TEXT,
    PRIMARY KEY (article_id, number)
);
CREATE INDEX IF NOT EXISTS revisions_key ON revisions(article_id, input_key);
CREATE TABLE IF NOT EXISTS acknowledgements (
    article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    check_id TEXT NOT NULL,
    note TEXT,
    acknowledged_by TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL,
    PRIMARY KEY (article_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS evidence_status (
    journal_id TEXT NOT NULL,
    index_name TEXT NOT NULL,
    criterion_id TEXT NOT NULL,
    status TEXT NOT NULL,            -- unverified | verified | not-met | not-applicable
    evidence_url TEXT,
    note TEXT,
    checked_on TEXT,
    verified_by TEXT,
    criteria_version TEXT,
    PRIMARY KEY (journal_id, index_name, criterion_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
"""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


@contextlib.contextmanager
def db() -> Iterator[sqlite3.Connection]:
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def log(article_id: str | None, kind: str, detail: Any = None) -> None:
    with db() as con:
        con.execute("INSERT INTO events(article_id, at, kind, detail) VALUES (?,?,?,?)",
                    (article_id, now_iso(), kind, json.dumps(detail, ensure_ascii=False) if detail is not None else None))


# ------------------------------------------------------------------ articles

def new_article_id() -> str:
    return f"art-{dt.date.today():%Y%m%d}-{secrets.token_hex(3)}"


def article_dir(article_id: str) -> Path:
    if not article_id.startswith("art-") or "/" in article_id or "\\" in article_id or ".." in article_id:
        raise ValueError(f"bad article id {article_id!r}")
    return config.articles_dir() / article_id


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_articles() -> list[sqlite3.Row]:
    with db() as con:
        return con.execute("""
            SELECT a.*, (SELECT MAX(number) FROM revisions r WHERE r.article_id=a.id) AS latest_rev,
                   (SELECT status FROM revisions r WHERE r.article_id=a.id ORDER BY number DESC LIMIT 1) AS latest_status,
                   (SELECT MAX(number) FROM revisions r WHERE r.article_id=a.id AND r.status='approved') AS approved_rev
            FROM articles a ORDER BY a.updated_at DESC""").fetchall()


def get_article(article_id: str) -> sqlite3.Row | None:
    with db() as con:
        return con.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()


def revisions(article_id: str) -> list[sqlite3.Row]:
    with db() as con:
        return con.execute("SELECT * FROM revisions WHERE article_id=? ORDER BY number", (article_id,)).fetchall()


def revision(article_id: str, number: int) -> sqlite3.Row | None:
    with db() as con:
        return con.execute("SELECT * FROM revisions WHERE article_id=? AND number=?", (article_id, number)).fetchone()


def revision_dir(article_id: str, number: int) -> Path:
    return article_dir(article_id) / "revisions" / f"r{number:03d}"


def acknowledgements(article_id: str) -> dict[str, sqlite3.Row]:
    with db() as con:
        rows = con.execute("SELECT * FROM acknowledgements WHERE article_id=?", (article_id,)).fetchall()
    return {r["fingerprint"]: r for r in rows}
