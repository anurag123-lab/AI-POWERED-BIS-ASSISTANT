"""
Route smoke test — run after every milestone.

Boots the Flask app with a test client, logs in, then GETs every GET-able
route (substituting `1` for int path params) and asserts the status is not
404/500. Exits non-zero if anything is broken.

    python tools/smoke_test.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as bis_app  # noqa: E402

SKIP_ENDPOINTS = {"static", "logout"}
OK = {200, 301, 302, 303, 304}
# endpoints with an <id> param where a 404 on the substituted id is legitimate
OK_404_ENDPOINTS = {"api_history_item"}

# POST endpoints to exercise with a minimal JSON body: (path, json)
POST_PROBES = [
    ("/api/products/search", {"query": "iron"}),
    ("/api/schemes/analyze", {"is_number": "IS 302-2-25"}),
    ("/api/isi/photo-check", {"text": "IS 302-2-25 CM/L-1234567"}),
    ("/api/chat", {"message": "What standard applies to an electric iron?"}),
]

# POST endpoints exercised with form data
POST_FORM_PROBES = [
    ("/set-language", {"lang": "en"}),
]


def iter_get_paths(client_app):
    for rule in client_app.url_map.iter_rules():
        if rule.endpoint in SKIP_ENDPOINTS:
            continue
        if "GET" not in (rule.methods or set()):
            continue
        path = rule.rule
        if "<" in path:
            # substitute simple converters
            import re
            path = re.sub(r"<[^:>]+:[^>]+>", "1", path)
            path = re.sub(r"<[^>]+>", "1", path)
        yield rule.endpoint, path


def main():
    bis_app.app.config["TESTING"] = True
    c = bis_app.app.test_client()

    # establish a session
    c.get("/auth/google")

    failures = []
    checked = 0

    for endpoint, path in sorted(set(iter_get_paths(bis_app.app)), key=lambda x: x[1]):
        try:
            r = c.get(path, follow_redirects=False)
        except Exception as exc:  # noqa: BLE001
            failures.append((path, f"EXCEPTION {type(exc).__name__}: {exc}"))
            continue
        checked += 1
        acceptable = set(OK) | ({404} if endpoint in OK_404_ENDPOINTS else set())
        flag = "ok " if r.status_code in acceptable else "FAIL"
        if r.status_code not in acceptable:
            failures.append((path, f"status {r.status_code}"))
        print(f"  [{flag}] GET  {path:42} {r.status_code}  ({endpoint})")

    for path, body in POST_PROBES:
        try:
            r = c.post(path, json=body, follow_redirects=False)
        except Exception as exc:  # noqa: BLE001
            failures.append((path, f"EXCEPTION {type(exc).__name__}: {exc}"))
            continue
        checked += 1
        flag = "ok " if r.status_code in OK else "FAIL"
        if r.status_code not in OK:
            failures.append((path, f"status {r.status_code}"))
        print(f"  [{flag}] POST {path:42} {r.status_code}")

    for path, form in POST_FORM_PROBES:
        try:
            r = c.post(path, data=form, follow_redirects=False)
        except Exception as exc:  # noqa: BLE001
            failures.append((path, f"EXCEPTION {type(exc).__name__}: {exc}"))
            continue
        checked += 1
        flag = "ok " if r.status_code in OK else "FAIL"
        if r.status_code not in OK:
            failures.append((path, f"status {r.status_code}"))
        print(f"  [{flag}] POST {path:42} {r.status_code}")

    print()
    if failures:
        print(f"SMOKE TEST FAILED — {len(failures)} of {checked} checks broke:")
        for path, why in failures:
            print(f"   {path}  ->  {why}")
        sys.exit(1)
    print(f"SMOKE TEST PASSED — {checked} routes OK")


if __name__ == "__main__":
    main()
