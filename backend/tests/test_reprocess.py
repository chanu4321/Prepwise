import pytest

from admin_cli import main as cli_main
from services.paper_store import PaperRow
from services.reprocess import merge_fields, reprocess_many, reprocess_paper

OLD = {"subject_code": "1T402", "subject_name": "DMBI", "semester": None, "year": "Nov, 2025", "time": "3 Hrs", "marks": "60"}


class Processor:
    def __init__(self, metadata, fail_on=None):
        self.metadata = metadata
        self.fail_on = fail_on
        self.full_text_calls = 0

    def process_pdf(self, path):
        if self.fail_on and self.fail_on in path:
            raise RuntimeError("OCR exploded")
        return {"text": "header", "metadata": dict(self.metadata)}

    def extract_full_text(self, path):
        self.full_text_calls += 1
        return "new full text"


class Vectors:
    def __init__(self, vector=(0.1, 0.2)):
        self.vector = list(vector) if vector else None
        self.upserts = []

    def get_embedding(self, text, input_type="passage"):
        assert input_type == "passage"
        return self.vector

    def upsert_paper_vector(self, paper_id, vector, text, metadata):
        self.upserts.append((paper_id, text, metadata))
        return True


@pytest.fixture
def row(tmp_path):
    pdf = tmp_path / "DMBI.pdf"
    pdf.write_bytes(b"%PDF")
    return PaperRow(id=14, filename="DMBI.pdf", file_path=str(pdf), fields=dict(OLD))


def test_merge_keeps_old_values_when_new_are_empty():
    new = {"subject_code": "IT402", "subject_name": None, "semester": None, "year": "", "time": None, "marks": None}
    merged = merge_fields(OLD, new)
    assert merged["subject_code"] == "IT402"
    assert merged["subject_name"] == "DMBI" and merged["year"] == "Nov, 2025" and merged["marks"] == "60"


def test_preview_writes_nothing(row):
    processor, vectors, updates = Processor({"subjectCode": "it 402"}), Vectors(), []
    result = reprocess_paper(row, processor, vectors, lambda pid, f: updates.append(pid), apply=False)
    assert result.status == "preview"
    assert result.changes == {"subject_code": ("1T402", "IT402")}
    assert updates == [] and vectors.upserts == [] and processor.full_text_calls == 0


def test_apply_updates_vector_and_metadata(row):
    vectors, updates = Vectors(), []
    result = reprocess_paper(row, Processor({"subjectCode": "it 402"}), vectors,
                             lambda pid, fields: updates.append((pid, fields)), apply=True)
    assert result.status == "updated"
    assert updates == [(14, {**OLD, "subject_code": "IT402"})]
    paper_id, text, payload = vectors.upserts[0]
    assert (paper_id, text) == (14, "new full text")
    assert payload == {"subject_code": "IT402", "subject_name": "DMBI", "year": "Nov, 2025", "filename": "DMBI.pdf"}


def test_embedding_failure_changes_nothing(row):
    updates = []
    result = reprocess_paper(row, Processor({"subjectCode": "it 402"}), Vectors(vector=None),
                             lambda pid, f: updates.append(pid), apply=True)
    assert result.status == "failed" and updates == []


def test_missing_pdf_is_skipped(row):
    missing = PaperRow(id=7, filename="gone.pdf", file_path="/nope/gone.pdf", fields=dict(OLD))
    assert reprocess_paper(missing, Processor({}), Vectors(), lambda *a: None, apply=True).status == "skipped"


def test_one_failure_does_not_stop_the_rest(row, tmp_path):
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"%PDF")
    bad = PaperRow(id=1, filename="bad.pdf", file_path=str(bad_pdf), fields=dict(OLD))
    lines = []
    results = reprocess_many([bad, row], Processor({"subjectCode": "it402"}, fail_on="bad"), Vectors(),
                             lambda *a: None, apply=True, out=lines.append)
    assert [r.status for r in results] == ["failed", "updated"]
    assert any("subject_code: '1T402' -> 'IT402'" in line for line in lines)


def test_cli_requires_ids_or_all():
    with pytest.raises(SystemExit):
        cli_main(["reprocess"])


# Metadata that maps (via to_paper_fields) to exactly OLD, so merge_fields produces no diff.
NO_CHANGE_METADATA = {"subjectCode": "1T402", "subjectName": "DMBI", "monthYear": "Nov, 2025", "time": "3 Hrs", "marks": "60"}


def test_preview_with_no_metadata_change_is_unchanged(row):
    processor, vectors, updates = Processor(NO_CHANGE_METADATA), Vectors(), []
    result = reprocess_paper(row, processor, vectors, lambda pid, f: updates.append(pid), apply=False)
    assert result.status == "unchanged"
    assert result.changes == {}
    assert result.message == "metadata already up to date"
    assert updates == [] and vectors.upserts == [] and processor.full_text_calls == 0


def test_apply_with_no_metadata_change_refreshes_vector_but_skips_metadata_write(row):
    vectors, updates = Vectors(), []
    result = reprocess_paper(row, Processor(NO_CHANGE_METADATA), vectors,
                             lambda pid, fields: updates.append((pid, fields)), apply=True)
    assert result.status == "unchanged"
    assert result.message == "metadata already up to date; search index refreshed"
    assert updates == []
    assert len(vectors.upserts) == 1
    paper_id, text, _ = vectors.upserts[0]
    assert (paper_id, text) == (14, "new full text")


def test_metadata_save_failure_is_reported_but_vector_already_updated(row):
    vectors = Vectors()

    def failing_update(pid, fields):
        raise RuntimeError("db exploded")

    result = reprocess_paper(row, Processor({"subjectCode": "it 402"}), vectors, failing_update, apply=True)
    assert result.status == "failed"
    assert result.changes == {"subject_code": ("1T402", "IT402")}
    assert "re-run" in result.message
    assert len(vectors.upserts) == 1
