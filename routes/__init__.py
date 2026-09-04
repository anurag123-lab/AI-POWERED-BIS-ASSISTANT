"""Importing this package registers every route on the shared Flask `app`
from server.py. Each submodule is organised by area — auth, onboarding,
workspace/home, feature pages, cases, the JSON api, admin and legacy
redirects — but every endpoint name and URL is unchanged from the original
single-file app.py, so `url_for(...)` calls in templates keep working as-is.
"""
from . import auth, onboarding, workspace, features, cases, api, admin, legacy  # noqa: F401
