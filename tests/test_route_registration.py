def _route_entries(app):
    entries = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            entries.append((method, path))
    return entries


def test_import_app_succeeds():
    from api.app.main import app

    assert app is not None


def test_expected_and_absent_routes():
    from api.app.main import app

    entries = _route_entries(app)

    assert ("POST", "/api/credits/number-renewals/enqueue-due") in entries
    assert ("POST", "/api/sms/billing/enqueue-due") not in entries


def test_inbound_preview_route_exists_once():
    from api.app.main import app

    entries = _route_entries(app)
    count = sum(1 for method, path in entries if method == "POST" and path == "/api/inbound/lines/preview")
    assert count == 1


def test_no_duplicate_method_path_combinations():
    from api.app.main import app

    entries = _route_entries(app)
    duplicates = {entry for entry in entries if entries.count(entry) > 1}
    assert not duplicates, f"Duplicate method/path combinations found: {sorted(duplicates)}"
