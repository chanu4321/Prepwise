import os

# Tests must never reach real services. python-dotenv does not override variables that are
# already set, so fixing them here (before any backend module loads .env) keeps tests offline.
os.environ["DATABASE_URL"] = "postgresql://tests-must-not-use-the-real-db@127.0.0.1:1/none"
os.environ["QDRANT_URL"] = "http://127.0.0.1:1"
os.environ["QDRANT_API_KEY"] = ""
os.environ["NVIDIA_API_KEY"] = "test-key"
os.environ["NVIDIA_EMBED_API_KEY"] = "test-key"
os.environ["AZURE_CLIENT_ID"] = "test-client-id"
os.environ["IP_HASH_SALT"] = "test-salt"
os.environ["GENERATE_LIMIT_TRIAL"] = "3"
os.environ["GENERATE_LIMIT_VERIFIED"] = "25"
os.environ["UPLOAD_LIMIT_ANON"] = "5"
os.environ["UPLOAD_LIMIT_STUDENT"] = "20"
os.environ["UPLOAD_LIMIT_FACULTY"] = "10"
os.environ["SYLLABUS_LIMIT"] = "10"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    from main import app

    # Not used as a context manager, so the lifespan (init_db against Postgres) never runs.
    yield TestClient(app)
    app.dependency_overrides.clear()
