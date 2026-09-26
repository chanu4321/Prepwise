from types import SimpleNamespace

import pytest

from services import vector_service
from services.vector_service import LIVE_ONLY, VECTOR_SIZE, VectorService


class FakeQdrant:
    def __init__(self, fail=False, collection_exists=True, vector_size=VECTOR_SIZE, has_status_index=True):
        self.calls = []
        self.fail = fail
        self.collection_exists = collection_exists
        self.vector_size = vector_size
        self.has_status_index = has_status_index

    def _record(self, *call):
        if self.fail:
            raise RuntimeError("qdrant down")
        self.calls.append(call)

    def upsert(self, collection_name, points):
        self._record("upsert", points)

    def query_points(self, collection_name, query, limit, with_payload, query_filter=None):
        self._record("query", query, limit, query_filter)
        return SimpleNamespace(points=[SimpleNamespace(id=7, score=0.9, payload={"full_text": "t"})])

    def set_payload(self, collection_name, payload, points):
        self._record("set_payload", payload, points)

    def delete(self, collection_name, points_selector):
        self._record("delete", points_selector.points)

    def retrieve(self, collection_name, ids, with_payload):
        self._record("retrieve", ids)
        return [SimpleNamespace(id=i, payload={"full_text": f"text {i}"}) for i in ids]

    # --- collection/index bootstrap (_ensure_collection) ---------------------------------------
    def get_collections(self):
        self.calls.append(("get_collections",))
        names = ["papers"] if self.collection_exists else []
        return SimpleNamespace(collections=[SimpleNamespace(name=n) for n in names])

    def get_collection(self, collection_name):
        self.calls.append(("get_collection", collection_name))
        vectors = SimpleNamespace(size=self.vector_size)
        schema = {"status": object()} if self.has_status_index else {}
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=vectors)),
                               payload_schema=schema)

    def create_collection(self, collection_name, vectors_config):
        self.calls.append(("create_collection", collection_name))

    def create_payload_index(self, collection_name, field_name, field_schema):
        self.calls.append(("create_payload_index", field_name))


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(VectorService, "_ensure_collection", lambda self: None)
    monkeypatch.setattr(vector_service, "embed_text", lambda text, input_type="passage": [0.1, 0.2])
    svc = VectorService()
    svc.client = FakeQdrant()
    return svc


# _ensure_collection isn't patched away here: these tests build VectorService() themselves so the
# real bootstrap logic runs against a FakeQdrant standing in for `database.qdrant_client`.
def _built_with(monkeypatch, fake):
    monkeypatch.setattr(vector_service, "qdrant_client", fake)
    svc = VectorService()
    assert svc.client is fake
    return svc


def test_ensure_collection_indexes_status_on_an_existing_collection_that_lacks_it(monkeypatch):
    fake = FakeQdrant(collection_exists=True, has_status_index=False)
    _built_with(monkeypatch, fake)
    assert [c for c in fake.calls if c[0] == "create_payload_index"] == [("create_payload_index", "status")]


def test_ensure_collection_leaves_an_existing_index_alone(monkeypatch):
    fake = FakeQdrant(collection_exists=True, has_status_index=True)
    _built_with(monkeypatch, fake)
    assert [c for c in fake.calls if c[0] == "create_payload_index"] == []


def test_ensure_collection_creates_the_collection_and_its_index_when_missing(monkeypatch):
    fake = FakeQdrant(collection_exists=False)
    _built_with(monkeypatch, fake)
    assert [c[0] for c in fake.calls if c[0] in ("create_collection", "create_payload_index", "get_collection")] == \
           ["create_collection", "create_payload_index"]


def test_every_stored_vector_carries_its_status(service):
    assert service.upsert_paper_vector(3, [0.1], "text", {"filename": "a.pdf"}, "review") is True
    (_, points), = service.client.calls
    assert points[0].payload == {"filename": "a.pdf", "full_text": "text", "status": "review"}


def test_public_search_only_returns_live_papers(service):
    service.search_similar("Software Project Management", 4)
    (_, _, limit, query_filter), = service.client.calls
    assert limit == 4 and query_filter == LIVE_ONLY
    condition = query_filter.must_not[0]
    assert condition.key == "status" and set(condition.match.any) == {"processing", "review", "rejected", "failed"}


def test_nearest_papers_searches_every_status_but_skips_the_paper_itself(service):
    points = service.nearest_papers([0.3], 5, exclude_id=12)
    (_, vector, limit, query_filter), = service.client.calls
    assert (vector, limit) == ([0.3], 5)
    assert query_filter.must_not[0].has_id == [12]
    assert [p.id for p in points] == [7]


def test_nearest_papers_raises_when_qdrant_is_down(service):
    service.client = FakeQdrant(fail=True)
    with pytest.raises(RuntimeError):
        service.nearest_papers([0.3], 5, exclude_id=12)


def test_payload_delete_and_texts(service):
    assert service.set_paper_payload(3, {"status": "live"}) is True
    assert service.delete_paper(3) is True
    assert service.get_paper_texts([1, 2]) == {1: "text 1", 2: "text 2"}
    assert service.get_paper_texts([]) == {}
    assert [c[0] for c in service.client.calls] == ["set_payload", "delete", "retrieve"]


def test_payload_delete_and_texts_report_failure(service):
    service.client = FakeQdrant(fail=True)
    assert service.set_paper_payload(3, {"status": "live"}) is False
    assert service.delete_paper(3) is False
    assert service.get_paper_texts([1]) == {}
