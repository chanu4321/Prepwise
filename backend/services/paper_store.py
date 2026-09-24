from database import db_cursor
from services.paper_metadata import PAPER_FIELDS


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
