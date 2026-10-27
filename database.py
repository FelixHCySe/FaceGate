"""SQLite-Datenbank: Personen, Gesichtsmerkmale (Embeddings) und Zugangsprotokoll.

Es werden bewusst KEINE Fotos gespeichert, nur die 128-dimensionalen
Merkmalsvektoren. Das ist datensparsamer und reicht für die Erkennung.
"""
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    birthdate   TEXT,                       -- ISO-Format YYYY-MM-DD
    permissions TEXT    NOT NULL DEFAULT '', -- kommagetrennt, z. B. "tuer,labor"
    active      INTEGER NOT NULL DEFAULT 1,
    created     TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS embeddings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    vector    BLOB    NOT NULL,
    created   TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS access_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,
    person_id INTEGER,
    name      TEXT    NOT NULL,
    score     REAL,
    granted   INTEGER NOT NULL,
    note      TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_ts ON access_log(ts);
"""


def parse_permissions(text):
    """'Tür, Labor ,tuer' -> ['tuer', 'labor'] (klein, ohne Duplikate, Umlaute ersetzt)."""
    result = []
    for part in (text or "").split(","):
        tag = ascii_fold(part.strip().lower())
        if tag and tag not in result:
            result.append(tag)
    return result


def ascii_fold(text):
    """Ersetzt Umlaute, weil die OpenCV-Schrift sie nicht darstellen kann."""
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
    return text.translate(table)


def age_from_birthdate(birthdate, today=None):
    if not birthdate:
        return None
    try:
        born = date.fromisoformat(birthdate)
    except ValueError:
        return None
    today = today or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('version', '0')")

    # Jede Thread bekommt eine eigene Verbindung (sqlite3 ist nicht thread-sicher)
    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            self._local.conn = conn
        return conn

    # ---- Versionszähler: der Erkenner lädt neu, sobald sich Personen ändern ----
    def get_version(self):
        row = self._conn().execute("SELECT value FROM meta WHERE key='version'").fetchone()
        return int(row["value"])

    def _bump(self, conn):
        conn.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key='version'")

    # ---- Personen ----
    def add_person(self, name, birthdate=None, permissions=""):
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO persons(name, birthdate, permissions, created) VALUES (?,?,?,?)",
                (name.strip(), birthdate or None, ",".join(parse_permissions(permissions)),
                 datetime.now().isoformat(timespec="seconds")),
            )
            self._bump(conn)
            return cur.lastrowid

    def update_person(self, person_id, name, birthdate, permissions, active):
        with self._conn() as conn:
            conn.execute(
                "UPDATE persons SET name=?, birthdate=?, permissions=?, active=? WHERE id=?",
                (name.strip(), birthdate or None, ",".join(parse_permissions(permissions)),
                 1 if active else 0, person_id),
            )
            self._bump(conn)

    def delete_person(self, person_id):
        with self._conn() as conn:
            conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
            self._bump(conn)

    def get_person(self, person_id):
        row = self._conn().execute(
            "SELECT p.*, (SELECT COUNT(*) FROM embeddings e WHERE e.person_id=p.id) AS samples "
            "FROM persons p WHERE p.id=?", (person_id,)).fetchone()
        return self._person_dict(row) if row else None

    def list_persons(self):
        rows = self._conn().execute(
            "SELECT p.*, (SELECT COUNT(*) FROM embeddings e WHERE e.person_id=p.id) AS samples "
            "FROM persons p ORDER BY p.name COLLATE NOCASE").fetchall()
        return [self._person_dict(r) for r in rows]

    @staticmethod
    def _person_dict(row):
        d = dict(row)
        d["permissions"] = [p for p in (d["permissions"] or "").split(",") if p]
        d["age"] = age_from_birthdate(d["birthdate"])
        d["active"] = bool(d["active"])
        return d

    # ---- Embeddings ----
    def add_embedding(self, person_id, vector):
        vec = np.asarray(vector, dtype=np.float32).ravel()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO embeddings(person_id, vector, created) VALUES (?,?,?)",
                (person_id, vec.tobytes(), datetime.now().isoformat(timespec="seconds")),
            )
            self._bump(conn)

    def clear_embeddings(self, person_id):
        with self._conn() as conn:
            conn.execute("DELETE FROM embeddings WHERE person_id=?", (person_id,))
            self._bump(conn)

    def load_embeddings(self):
        """Gibt (person_ids, matrix) aller aktiven Personen zurück."""
        rows = self._conn().execute(
            "SELECT e.person_id, e.vector FROM embeddings e "
            "JOIN persons p ON p.id = e.person_id WHERE p.active = 1").fetchall()
        if not rows:
            return np.empty(0, dtype=np.int64), np.empty((0, 128), dtype=np.float32)
        ids = np.array([r["person_id"] for r in rows], dtype=np.int64)
        matrix = np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        return ids, matrix

    # ---- Protokoll ----
    def log_access(self, person_id, name, score, granted, note=""):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO access_log(ts, person_id, name, score, granted, note) VALUES (?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), person_id, name,
                 None if score is None else round(float(score), 3), 1 if granted else 0, note),
            )

    def recent_log(self, limit=50):
        rows = self._conn().execute(
            "SELECT * FROM access_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def purge_old_log(self, days):
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute("DELETE FROM access_log WHERE ts < ?", (cutoff,))
