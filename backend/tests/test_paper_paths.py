from pathlib import Path

from services import paper_store
from services.paper_store import PAPERS_DIR, resolve_paper_path


def test_stored_paths_resolve_against_the_project_root():
    root = paper_store.PROJECT_ROOT
    assert (root / "backend" / "main.py").is_file()
    assert resolve_paper_path("backend/papers/SPM 23.pdf") == str(root / "backend" / "papers" / "SPM 23.pdf")


def test_resolution_ignores_the_current_directory(monkeypatch, tmp_path):
    expected = resolve_paper_path(PAPERS_DIR)
    monkeypatch.chdir(tmp_path)
    assert resolve_paper_path(PAPERS_DIR) == expected


def test_absolute_paths_are_kept(tmp_path):
    pdf = tmp_path / "x.pdf"
    assert resolve_paper_path(str(pdf)) == str(pdf)


def test_upload_folder_is_the_absolute_papers_folder():
    from api import routes

    assert Path(routes.processor.upload_dir).is_absolute()
    assert routes.processor.upload_dir == resolve_paper_path(PAPERS_DIR)
