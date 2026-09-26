import os
import psycopg2
from contextlib import contextmanager
from qdrant_client import QdrantClient
from dotenv import load_dotenv

load_dotenv()

# PostgreS (NeonDB)
def get_db_connection():
    conn_str = os.getenv("DATABASE_URL")
    if not conn_str:
        raise ValueError("DATABASE_URL is not set in .env")
    return psycopg2.connect(conn_str)

# Qdrant Client
qdrant_url = os.getenv("QDRANT_URL")
qdrant_api_key = os.getenv("QDRANT_API_KEY")

if qdrant_url and qdrant_api_key:
    qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
elif qdrant_url:
    qdrant_client = QdrantClient(url=qdrant_url)
else:
    qdrant_client = QdrantClient(host="localhost", port=6333)

@contextmanager
def db_cursor():
    """Yields a cursor; commits on success, rolls back on error, always closes the connection."""
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()


def init_db():
    """Creates or migrates all tables. Safe to run repeatedly (runs at app startup)."""
    with db_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS papers (
                id SERIAL PRIMARY KEY,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                subject_code TEXT,
                subject_name TEXT,
                semester TEXT,
                year TEXT,
                time TEXT,
                marks TEXT,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS syllabi (
                id SERIAL PRIMARY KEY,
                subject_code TEXT UNIQUE NOT NULL,
                subject_name TEXT,
                modules JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                ms_tid        TEXT NOT NULL,
                ms_oid        TEXT NOT NULL,
                email         TEXT,
                name          TEXT,
                role          TEXT CHECK (role IN ('student', 'faculty', 'admin')),
                verified      BOOLEAN NOT NULL DEFAULT FALSE,
                created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at  TIMESTAMP,
                UNIQUE (ms_tid, ms_oid)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_daily (
                subject  TEXT NOT NULL,
                action   TEXT NOT NULL CHECK (action IN ('generate', 'upload', 'syllabus')),
                day      DATE NOT NULL,
                count    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (subject, action, day)
            );
        """)
        cur.execute("ALTER TABLE papers ADD COLUMN IF NOT EXISTS uploaded_by INTEGER REFERENCES users(id);")
        cur.execute("ALTER TABLE syllabi ADD COLUMN IF NOT EXISTS uploaded_by INTEGER REFERENCES users(id);")

        # Upload moderation (Part B): every existing paper becomes 'live'
        cur.execute("""
            ALTER TABLE papers
                ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'live'
                    CHECK (status IN ('processing', 'live', 'review', 'rejected', 'failed')),
                ADD COLUMN IF NOT EXISTS review_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
                ADD COLUMN IF NOT EXISTS status_note TEXT,
                ADD COLUMN IF NOT EXISTS file_sha256 TEXT,
                ADD COLUMN IF NOT EXISTS upload_key TEXT,
                ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS retry_at TIMESTAMP,
                ADD COLUMN IF NOT EXISTS reviewed_by INTEGER REFERENCES users(id),
                ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP;
        """)
        # One stored copy per file; a failed upload doesn't block uploading the same file again
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS papers_active_sha256 ON papers (file_sha256) "
                    "WHERE status <> 'failed';")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS papers_upload_key ON papers (upload_key);")
        cur.execute("CREATE INDEX IF NOT EXISTS papers_status ON papers (status);")
