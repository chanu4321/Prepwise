from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import BaseModel

from errors import GENERIC_ERROR_MESSAGE, ApiError, install_error_handling

ORIGIN = "http://localhost:3000"


class SampleRequest(BaseModel):
    value: int


def make_app():
    app = FastAPI()
    install_error_handling(app)
    app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN], allow_methods=["*"], allow_headers=["*"])

    @app.get("/boom")
    def boom():
        raise RuntimeError("password=hunter2 leaked from the database driver")

    @app.get("/limited")
    def limited():
        raise ApiError(429, "quota_exceeded", "Daily limit reached.", headers={"Retry-After": "120"})

    @app.post("/validate")
    def validate(body: SampleRequest):
        return {"ok": True}

    return app


def test_unexpected_error_returns_generic_body_with_cors_headers():
    response = TestClient(make_app()).get("/boom", headers={"Origin": ORIGIN})
    assert response.status_code == 500
    assert response.json() == {"detail": GENERIC_ERROR_MESSAGE, "code": "internal_error"}
    assert "hunter2" not in response.text
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_api_error_uses_the_standard_shape_and_keeps_headers():
    response = TestClient(make_app()).get("/limited", headers={"Origin": ORIGIN})
    assert response.status_code == 429
    assert response.json() == {"detail": "Daily limit reached.", "code": "quota_exceeded"}
    assert response.headers["retry-after"] == "120"
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_main_app_exposes_retry_after_to_the_browser(client):
    response = client.get("/health", headers={"Origin": ORIGIN})
    assert "retry-after" in response.headers.get("access-control-expose-headers", "").lower()


def test_request_validation_error_returns_standard_shape_with_cors_headers():
    response = TestClient(make_app()).post("/validate", json={"value": "not_an_int"}, headers={"Origin": ORIGIN})
    assert response.status_code == 422
    assert response.json() == {"detail": "Some of the information sent was missing or invalid.", "code": "invalid_request"}
    assert response.headers["access-control-allow-origin"] == ORIGIN
