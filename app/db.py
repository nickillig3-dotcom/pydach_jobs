import sqlite3
from contextlib import contextmanager
from typing import Iterable

from .config import DB_PATH

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def get_connection():
    # Keine automatische Timestamp-Konvertierung; wir arbeiten mit Strings
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = dict_factory
    return conn

@contextmanager
def db() -> Iterable[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT,
            email TEXT,
            logo_url TEXT,
            description TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_featured INTEGER DEFAULT 0,
            grace_expires_at TIMESTAMP,
            status TEXT DEFAULT 'published'
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            price_cents INTEGER NOT NULL,
            currency TEXT NOT NULL,
            reference TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            paid_at TIMESTAMP,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_reference ON orders(reference)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_featured ON jobs(is_featured)")
