from dataclasses import dataclass
from pathlib import Path

from database import db_cursor
from services.paper_metadata import PAPER_FIELDS

# The folder that holds `backend/` (/app in the container). `papers.file_path` values are relative to it.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAPERS_DIR = "backend/papers"


def resolve_paper_path(stored_path: str) -> str:
    """Absolute path for a `papers.file_path` value, whatever directory the process was started from."""
    path = Path(stored_path)
    return str(path if path.is_absolute() else PROJECT_ROOT / path)


@dataclass
class PaperRow:
    id: int
    filename: str
    file_path: str
    fields: dict  # keys: PAPER_FIELDS


def insert_paper(filename: str, file_path: str, fields: dict, uploaded_by: int | None) -> int:
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO papers (filename, file_path, subject_code, subject_name, semester, year, time, marks, uploaded_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (filename, file_path, *(fields[k] for k in PAPER_FIELDS), uploaded_by),
        )
        return cur.fetchone()[0]


def get_papers(ids: list[int] | None) -> list[PaperRow]:
    """All papers (ids=None) or the given ids, ordered by id."""
    query = "SELECT id, filename, file_path, subject_code, subject_name, semester, year, time, marks FROM papers"
    params: tuple = ()
    if ids is not None:
        query += " WHERE id = ANY(%s)"
        params = (list(ids),)
    query += " ORDER BY id"
    with db_cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [PaperRow(id=r[0], filename=r[1], file_path=r[2], fields=dict(zip(PAPER_FIELDS, r[3:9]))) for r in rows]


def update_paper_metadata(paper_id: int, fields: dict) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE papers SET subject_code = %s, subject_name = %s, semester = %s, year = %s, time = %s, marks = %s "
            "WHERE id = %s",
            (*(fields[k] for k in PAPER_FIELDS), paper_id),
        )
