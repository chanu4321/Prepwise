import os
import psycopg2
from qdrant_client import QdrantClient
from dotenv import load_dotenv

load_dotenv()

# PostgreS (NeonDB)
def get_db_connection():
    conn_str = os.getenv("DATABASE_URL")
    if not conn_str:
        raise ValueError("DATABASE_URL is not set in .env")
    return psycopg2.connect(conn_str)

# Qdrant (Local)
# Assuming Qdrant is running on default port 6333
qdrant_client = QdrantClient(host="localhost", port=6333)

def init_db():
    """Initializes tables in PostgreSQL."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create Papers Table
    cursor.execute("""
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
    
    conn.commit()
    cursor.close()
    conn.close()
