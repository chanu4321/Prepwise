EXPECTED_ROUTES = {
    ("POST", "/api/v1/documents/ingest"),
    ("POST", "/api/v1/syllabus/upload"),
    ("GET", "/api/v1/syllabus/{subject_code}"),
    ("GET", "/api/v1/documents"),
    ("GET", "/api/v1/documents/{paper_id}/download"),
    ("POST", "/api/v1/search/semantic"),
    ("POST", "/api/v1/generate/mock-paper"),
    ("POST", "/api/v1/generate/mock-paper-stream"),
    ("GET", "/health"),
}


def registered_routes(app):
    routes = set()
    for route in app.routes:
        if hasattr(route, "methods"):
            for method in route.methods:
                routes.add((method, route.path))
        elif hasattr(route, "original_router"):
            # Handle _IncludedRouter objects
            for subroute in route.original_router.routes:
                for method in getattr(subroute, "methods", set()):
                    # Include the prefix from the router
                    prefix = route.include_context.prefix if hasattr(route, "include_context") else ""
                    routes.add((method, prefix + subroute.path))
    return routes


def test_app_imports_and_health_is_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_all_expected_routes_are_registered(client):
    missing = EXPECTED_ROUTES - registered_routes(client.app)
    assert not missing, f"missing routes: {sorted(missing)}"
