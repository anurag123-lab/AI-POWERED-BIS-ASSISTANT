"""
Single OpenAI access point for the app.

This is the ONLY module that imports `openai`. Everything else calls
`embed()` / `chat()` / `translate()` here. Every call is wrapped so that a
missing key, a network error, or a timeout never raises into the request path —
callers get a sentinel / fallback and the app keeps working on the offline
engine.

Env:
  OPENAI_API_KEY          - if unset/blank, llm_available() is False
  OPENAI_MODEL            - chat model         (default: gpt-4o-mini)
  OPENAI_EMBEDDING_MODEL  - embedding model    (default: text-embedding-3-small)
"""

import os
import time
import threading

_CHAT_TIMEOUT = 20.0
_EMBED_TIMEOUT = 8.0

# Circuit breaker: after this many consecutive failures, stop calling OpenAI
# for _COOLDOWN seconds so the request path isn't slowed by dead calls
# (e.g. an exhausted credit balance -> 429 on every request).
_FAIL_LIMIT = 3
_COOLDOWN = 300.0
_consec_fails = 0
_open_until = 0.0

# Sentinel returned by chat()/translate() when the model could not be reached
# or explicitly could not answer from the given context.
UNAVAILABLE = "__LLM_UNAVAILABLE__"
NOT_COVERED = "NOT_COVERED"

_client = None
_client_lock = threading.Lock()
_init_error = None


def _key():
    return (os.getenv("OPENAI_API_KEY") or "").strip()


def chat_model():
    return (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()


def embed_model():
    return (os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small").strip()


def _get_client():
    """Lazily build a shared OpenAI client. Returns None if unavailable."""
    global _client, _init_error
    if _client is not None:
        return _client
    if not _key():
        _init_error = "OPENAI_API_KEY not set"
        return None
    with _client_lock:
        if _client is None:
            try:
                from openai import OpenAI
                # max_retries=0: fail fast, we handle fallback ourselves
                _client = OpenAI(api_key=_key(), max_retries=0)
            except Exception as exc:  # noqa: BLE001
                _init_error = f"{type(exc).__name__}: {exc}"
                _client = None
    return _client


def _circuit_open():
    return time.time() < _open_until


def _record(success):
    global _consec_fails, _open_until
    if success:
        _consec_fails = 0
        _open_until = 0.0
    else:
        _consec_fails += 1
        if _consec_fails >= _FAIL_LIMIT:
            _open_until = time.time() + _COOLDOWN
            print(f"[LLM] circuit opened for {int(_COOLDOWN)}s after "
                  f"{_consec_fails} consecutive failures")


def llm_available():
    return _get_client() is not None and not _circuit_open()


def embed(text):
    """Return an embedding vector for `text`, or None on any failure."""
    client = _get_client()
    if client is None or _circuit_open():
        return None
    try:
        resp = client.with_options(timeout=_EMBED_TIMEOUT).embeddings.create(
            model=embed_model(),
            input=(text or "")[:8000],
        )
        _record(True)
        return resp.data[0].embedding
    except Exception as exc:  # noqa: BLE001
        _record(False)
        print(f"[LLM:embed] {type(exc).__name__}: {exc}")
        return None


def chat(system, user, *, temperature=0.2, max_tokens=700):
    """
    Run a single chat completion. Returns the assistant text, or the
    UNAVAILABLE sentinel on any failure. Callers must handle UNAVAILABLE.
    """
    client = _get_client()
    if client is None or _circuit_open():
        return UNAVAILABLE
    try:
        resp = client.with_options(timeout=_CHAT_TIMEOUT).chat.completions.create(
            model=chat_model(),
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        _record(True)
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        _record(False)
        print(f"[LLM:chat] {type(exc).__name__}: {exc}")
        return UNAVAILABLE


_LANG_NAME = {"hi": "Hindi", "te": "Telugu", "en": "English"}


def translate(text, target_lang):
    """
    Translate `text` into hi/te. IS numbers, clause numbers, URLs and
    bracketed citations must be left exactly as-is. Returns the original
    text unchanged if the model is unavailable or target is English.
    """
    target_lang = (target_lang or "en").lower()
    if target_lang == "en" or not (text or "").strip():
        return text
    lang = _LANG_NAME.get(target_lang)
    if not lang:
        return text
    out = chat(
        system=(
            f"You are a precise translator into {lang}. Translate the user's text "
            f"into {lang}. Keep unchanged, verbatim: Indian Standard numbers "
            f"(e.g. IS 302-2-3), clause/section numbers, URLs, email addresses, "
            f"and anything inside square brackets. Output only the translation."
        ),
        user=text,
        temperature=0.0,
        max_tokens=1200,
    )
    return text if out == UNAVAILABLE else out


def check_connectivity():
    """One tiny call at startup. Returns (ok: bool, detail: str)."""
    if not _key():
        return False, "OPENAI_API_KEY not set — running on offline engine"
    client = _get_client()
    if client is None:
        return False, f"client init failed ({_init_error})"
    try:
        client.with_options(timeout=_EMBED_TIMEOUT).embeddings.create(
            model=embed_model(), input="ping"
        )
        _record(True)
        return True, f"connected (chat={chat_model()}, embed={embed_model()})"
    except Exception as exc:  # noqa: BLE001
        # Trip the breaker now so live requests don't each eat a dead call.
        global _open_until
        _open_until = time.time() + _COOLDOWN
        msg = str(exc)
        if "insufficient_quota" in msg or "credit balance" in msg.lower():
            return False, "key valid but OpenAI account has no credits - offline engine active"
        return False, f"{type(exc).__name__}: {msg[:160]}"
