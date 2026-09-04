"""Entry point. All Flask app setup (env, DB, context processors, the
onboarding gate) lives in server.py; every route lives in routes/, grouped by
area (auth, onboarding, workspace/home, feature pages, cases, api, admin,
legacy redirects). Run with `python app.py` for the dev server, or point any
WSGI server (gunicorn/waitress) at `app:app`.
"""
from server import app
import routes  # noqa: F401 - imported for its side effect: registers every view on `app`

if __name__ == '__main__':
    print("\n=======================================================")
    print("  [STARTING] BIS COMPLIANCE COPILOT PLATFORM          ")
    print("  Access Web App at: http://127.0.0.1:5000           ")
    print("=======================================================\n")
    app.run(host='127.0.0.1', port=5000, debug=True)
