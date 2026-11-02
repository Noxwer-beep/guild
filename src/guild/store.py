"""SQLite: welche Briefings es gibt, welche Runden gemessen wurden, was passiert ist.

Jede Methode öffnet ihre eigene Verbindung. Flask bedient Anfragen in Threads, und eine
Verbindung darf nur in dem Thread benutzt werden, in dem sie entstanden ist — ein
geteilter `sqlite3.Connection` wirft unter Last `ProgrammingError`. Verbindungen sind
billig, geteilte Zustände nicht.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    slug        TEXT PRIMARY KEY,
    kunde       TEXT NOT NULL,
    produkt     TEXT NOT NULL,
    angelegt    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rounds (
    slug        TEXT NOT NULL,
    runde       TEXT NOT NULL,
    score       INTEGER NOT NULL,
    bestanden   INTEGER NOT NULL,
    kritisch    INTEGER NOT NULL,
    warnungen   INTEGER NOT NULL,
    dateien     INTEGER NOT NULL,
    gemessen    TEXT NOT NULL,
    qa          TEXT NOT NULL,
    PRIMARY KEY (slug, runde)
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    slug        TEXT NOT NULL,
    level       TEXT NOT NULL,
    text        TEXT NOT NULL
);
"""


def jetzt() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, pfad: Path | None = None) -> None:
        self.pfad = Path(pfad or config.DB)
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as db:
            db.executescript(SCHEMA)
            db.commit()

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.pfad, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def close(self) -> None:
        """Nichts zu schließen — jede Methode schließt selbst."""

    def _write(self, sql: str, werte: tuple = ()) -> None:
        with closing(self._conn()) as db:
            db.execute(sql, werte)
            db.commit()

    def _rows(self, sql: str, werte: tuple = ()) -> list[dict]:
        with closing(self._conn()) as db:
            return [dict(r) for r in db.execute(sql, werte).fetchall()]

    # ---------------------------------------------------------------- jobs

    def upsert_job(self, slug: str, kunde: str, produkt: str) -> None:
        self._write(
            "INSERT INTO jobs (slug, kunde, produkt, angelegt) VALUES (?,?,?,?) "
            "ON CONFLICT(slug) DO UPDATE SET kunde=excluded.kunde, produkt=excluded.produkt",
            (slug, kunde, produkt, jetzt()))

    def jobs(self) -> list[dict]:
        return self._rows("SELECT * FROM jobs ORDER BY angelegt DESC")

    def job(self, slug: str) -> dict | None:
        reihen = self._rows("SELECT * FROM jobs WHERE slug=?", (slug,))
        return reihen[0] if reihen else None

    # ---------------------------------------------------------------- rounds

    def save_qa(self, slug: str, ergebnis) -> None:
        self._write(
            "INSERT INTO rounds (slug, runde, score, bestanden, kritisch, warnungen, "
            "dateien, gemessen, qa) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(slug, runde) DO UPDATE SET score=excluded.score, "
            "bestanden=excluded.bestanden, kritisch=excluded.kritisch, "
            "warnungen=excluded.warnungen, dateien=excluded.dateien, "
            "gemessen=excluded.gemessen, qa=excluded.qa",
            (slug, ergebnis.runde, ergebnis.score, int(ergebnis.bestanden),
             ergebnis.kritisch, ergebnis.warnungen, ergebnis.dateien, jetzt(),
             json.dumps(ergebnis.as_dict(), ensure_ascii=False)))

    def rounds(self, slug: str) -> list[dict]:
        return self._rows("SELECT * FROM rounds WHERE slug=? ORDER BY runde", (slug,))

    def round(self, slug: str, runde: str) -> dict | None:
        reihen = self._rows("SELECT * FROM rounds WHERE slug=? AND runde=?", (slug, runde))
        return reihen[0] if reihen else None

    def latest(self, slug: str) -> dict | None:
        reihen = self._rows(
            "SELECT * FROM rounds WHERE slug=? ORDER BY runde DESC LIMIT 1", (slug,))
        return reihen[0] if reihen else None

    # ---------------------------------------------------------------- events

    def log(self, slug: str, level: str, text: str) -> None:
        self._write("INSERT INTO events (at, slug, level, text) VALUES (?,?,?,?)",
                    (jetzt(), slug, level, text))

    def events(self, slug: str | None = None, limit: int = 30) -> list[dict]:
        if slug:
            return self._rows(
                "SELECT * FROM events WHERE slug=? ORDER BY id DESC LIMIT ?", (slug, limit))
        return self._rows("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
