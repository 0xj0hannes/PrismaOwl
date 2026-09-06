import sqlite3
import json
import os
from contextlib import contextmanager

DB_PATH = "data/prisma.db"

def init_db():
    # Ensure data directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # We store the Pydantic models dumped as JSON in the data column for ease of flexibility,
        # and pull out any queryable fields (like is_duplicate, decision, human_reviewed) into actual columns.
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY,
                is_duplicate BOOLEAN,
                duplicate_of TEXT,
                data TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS screening_results (
                record_id TEXT PRIMARY KEY,
                decision TEXT,
                human_reviewed BOOLEAN DEFAULT 0,
                data TEXT,
                FOREIGN KEY (record_id) REFERENCES records (id)
            )
        ''')

        # Harvest runs (API searches) live in the database too; their records
        # go straight into `records` with harvest_id pointing back here.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS harvests (
                id TEXT PRIMARY KEY,
                source TEXT,
                status TEXT,
                started TEXT,
                data TEXT
            )
        ''')
        # Migration for databases created before harvests were stored.
        cols = {row[1] for row in cursor.execute("PRAGMA table_info(records)").fetchall()}
        if "harvest_id" not in cols:
            cursor.execute("ALTER TABLE records ADD COLUMN harvest_id TEXT")

        # Project documents (inclusion criteria, search strategy) live here too.
        _ensure_documents(conn)
        conn.commit()

    _import_legacy_files()


def _ensure_documents(conn) -> None:
    conn.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            key TEXT PRIMARY KEY,
            data TEXT,
            updated TEXT
        )
    ''')


def _import_legacy_files() -> None:
    """One-time migration: criteria.json / search_strategy.json in the project
    root (the pre-database storage) are imported when the database has no
    such document yet. The files are left in place and ignored afterwards."""
    from .config import CRITERIA_PATH, SEARCH_STRATEGY_PATH   # local import: config imports db
    for key, path in (("criteria", CRITERIA_PATH), ("search_strategy", SEARCH_STRATEGY_PATH)):
        if get_document(key) is not None or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data:
            save_document(key, data)


def get_document(key: str):
    """A stored JSON document (dict) or None."""
    with get_db() as conn:
        _ensure_documents(conn)
        row = conn.execute('SELECT data FROM documents WHERE key = ?', (key,)).fetchone()
        return json.loads(row['data']) if row else None


def save_document(key: str, data: dict) -> None:
    from datetime import datetime
    with get_db() as conn:
        _ensure_documents(conn)
        conn.execute('INSERT OR REPLACE INTO documents (key, data, updated) VALUES (?, ?, ?)',
                     (key, json.dumps(data, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))
        conn.commit()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def save_record(record_data: dict):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO records (id, is_duplicate, duplicate_of, harvest_id, data) VALUES (?, ?, ?, ?, ?)',
            (
                record_data['id'],
                record_data.get('is_duplicate', False),
                record_data.get('duplicate_of'),
                record_data.get('harvest_id'),
                json.dumps(record_data)
            )
        )
        conn.commit()

def save_screening_result(result_data: dict):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO screening_results (record_id, decision, human_reviewed, data) VALUES (?, ?, ?, ?)',
            (
                result_data['record_id'],
                result_data.get('decision'),
                1 if result_data.get('human_reviewed') else 0,
                json.dumps(result_data)
            )
        )
        conn.commit()

def get_all_records():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT data FROM records')
        return [json.loads(row['data']) for row in cursor.fetchall()]

def get_all_screening_results():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT data FROM screening_results')
        return {json.loads(row['data'])['record_id']: json.loads(row['data']) for row in cursor.fetchall()}

def get_unique_records():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT data FROM records WHERE is_duplicate = 0 OR is_duplicate IS NULL')
        return [json.loads(row['data']) for row in cursor.fetchall()]

# ---------------------------------------------------------------------------
# Harvest runs
# ---------------------------------------------------------------------------

def save_harvest(run: dict) -> None:
    """Insert or update a harvest run (``run['id']`` required)."""
    with get_db() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO harvests (id, source, status, started, data) VALUES (?, ?, ?, ?, ?)',
            (run['id'], run.get('source'), run.get('status'), run.get('started'), json.dumps(run)))
        conn.commit()


def get_harvests() -> list:
    """All harvest runs, newest first."""
    with get_db() as conn:
        rows = conn.execute('SELECT data FROM harvests ORDER BY started DESC').fetchall()
        return [json.loads(r['data']) for r in rows]


def get_harvest(run_id: str):
    with get_db() as conn:
        row = conn.execute('SELECT data FROM harvests WHERE id = ?', (run_id,)).fetchone()
        return json.loads(row['data']) if row else None


def get_records_for_harvest(run_id: str) -> list:
    with get_db() as conn:
        rows = conn.execute('SELECT data FROM records WHERE harvest_id = ?', (run_id,)).fetchall()
        return [json.loads(r['data']) for r in rows]


def delete_harvest(run_id: str) -> dict:
    """Delete a harvest run and the records it brought in, except records that
    already have a screening result (those stay, detached from the run, so the
    audit trail is never broken). Returns the counts."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('''DELETE FROM records WHERE harvest_id = ?
                       AND id NOT IN (SELECT record_id FROM screening_results)''', (run_id,))
        removed = cur.rowcount
        cur.execute('UPDATE records SET harvest_id = NULL WHERE harvest_id = ?', (run_id,))
        kept = cur.rowcount
        cur.execute('DELETE FROM harvests WHERE id = ?', (run_id,))
        deleted_run = cur.rowcount
        conn.commit()
    return {"deleted_run": bool(deleted_run), "records_removed": removed, "records_kept": kept}


def clear_corpus() -> dict:
    """Delete every ingested record (canonical and duplicate), every harvest
    run and every screening result. Returns the counts removed."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM screening_results')
        results = cur.rowcount
        cur.execute('DELETE FROM records')
        records = cur.rowcount
        cur.execute('DELETE FROM harvests')
        harvests = cur.rowcount
        conn.commit()
    return {"records": records, "harvests": harvests, "screening_results": results}


def clear_screening_results() -> int:
    """Delete all screening results. Returns the number of rows removed."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM screening_results')
        conn.commit()
        return cursor.rowcount
