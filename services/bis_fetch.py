"""
Best-effort HTTP fetch for bis.gov.in / lims.bis.gov.in / crsbis.in with an
on-disk cache. Used by tools/build_kb.py and the /admin/refresh-kb route.

Never raises: returns (ok, text_or_none, detail). The curated knowledge base
is the always-available fallback when a fetch fails.
"""

import hashlib
import os
import time

_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "knowledge_base", "_cache",
)
_UA = "BIS-Assistant-KB-Builder/1.0 (+educational SIH project)"
_DEFAULT_TTL = 7 * 24 * 3600  # 7 days


def _cache_path(url):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(_CACHE_DIR, h + ".txt")


def fetch(url, ttl=_DEFAULT_TTL, timeout=20):
    """Return (ok, text|None, detail). Uses a fresh-enough cache file if present."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cp = _cache_path(url)
    if os.path.exists(cp) and (time.time() - os.path.getmtime(cp)) < ttl:
        try:
            with open(cp, "r", encoding="utf-8") as fh:
                return True, fh.read(), "cache"
        except Exception:  # noqa: BLE001
            pass
    try:
        import requests
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        r.raise_for_status()
        text = r.text
        try:
            with open(cp, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception:  # noqa: BLE001
            pass
        return True, text, f"fetched {r.status_code} ({len(text)} bytes)"
    except Exception as exc:  # noqa: BLE001
        # fall back to a stale cache file if we have one
        if os.path.exists(cp):
            try:
                with open(cp, "r", encoding="utf-8") as fh:
                    return True, fh.read(), f"stale-cache ({type(exc).__name__})"
            except Exception:  # noqa: BLE001
                pass
        return False, None, f"{type(exc).__name__}: {exc}"


def head_ok(url, timeout=15):
    """True if the URL resolves (HEAD, falling back to a tiny GET)."""
    try:
        import requests
        r = requests.head(url, headers={"User-Agent": _UA}, timeout=timeout, allow_redirects=True)
        if r.status_code < 400:
            return True
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout, stream=True)
        return r.status_code < 400
    except Exception:  # noqa: BLE001
        return False
