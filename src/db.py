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
            'INSERT OR REPLACE INTO records (id, is_duplicate, duplicate_of, data) VALUES (?, ?, ?, ?)',
            (
                record_data['id'], 
                record_data.get('is_duplicate', False),
                record_data.get('duplicate_of'),
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

def clear_screening_results() -> int:
    """Delete all screening results. Returns the number of rows removed."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM screening_results')
        conn.commit()
        return cursor.rowcount
