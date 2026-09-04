"""
LLM access point for the app - Google Gemini only.

This is the ONLY module the rest of the app imports for embeddings / chat /
translation.

    embed / chat  -> Gemini  (GEMINI_API_KEY, free tier)  or offline fallback
    translate     -> deep-translator (no key) -> Gemini -> original English

Every call is wrapped: a missing key, a quota error, or a timeout never raises
into the request path. A short circuit-breaker stops calling a dead endpoint so
requests are not slowed by it. Indian Standard numbers, URLs and [bracketed]
citations are protected from translation.
"""

import os
import re
import time
import threading

_CHAT_TIMEOUT = 30.0
_FAIL_LIMIT = 4
_COOLDOWN = 180.0

UNAVAILABLE = "__LLM_UNAVAILABLE__"
NOT_COVERED = "NOT_COVERED"

_lock = threading.Lock()
_client = None
_fails = 0
_open_until = 0.0
_last_err = ""


# ---------------------------------------------------------------- config

def _key():
    return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()


def chat_model():
    return (os.getenv("GEMINI_MODEL") or "gemini-flash-lite-latest").strip()


def embed_model():
    return (os.getenv("GEMINI_EMBEDDING_MODEL") or "gemini-embedding-001").strip()


def active_provider():
    return "gemini" if _get_client() is not None else None


# ---------------------------------------------------------------- breaker

def _circuit_open():
    return time.time() < _open_until


def _record(success):
    global _fails, _open_until, _last_err
    if success:
        _fails, _open_until = 0, 0.0
    else:
        _fails += 1
        if _fails >= _FAIL_LIMIT:
            _open_until = time.time() + _COOLDOWN
            print(f"[LLM] Gemini circuit opened for {int(_COOLDOWN)}s")


# ---------------------------------------------------------------- client

def _get_client():
    global _client, _last_err
    if not _key() or _circuit_open():
        return None
    if _client is None:
        with _lock:
            if _client is None:
                try:
                    from google import genai
                    _client = genai.Client(api_key=_key())
                except Exception as exc:  # noqa: BLE001
                    _last_err = f"{type(exc).__name__}: {exc}"
                    _client = None
    return _client


def llm_available():
    return _get_client() is not None


# ---------------------------------------------------------------- embeddings

def embed(text):
    """Gemini embedding, or None so the caller uses a local hashed vector."""
    cli = _get_client()
    if cli is None:
        return None
    try:
        r = cli.models.embed_content(model=embed_model(), contents=(text or "")[:8000])
        _record(True)
        return list(r.embeddings[0].values)
    except Exception as exc:  # noqa: BLE001
        _record(False)
        print(f"[LLM:embed] {type(exc).__name__}: {exc}")
        return None


# ---------------------------------------------------------------- chat

def chat(system, user, *, temperature=0.2, max_tokens=700):
    """Single completion. Returns text, or UNAVAILABLE on any failure."""
    cli = _get_client()
    if cli is None:
        return UNAVAILABLE
    try:
        from google.genai import types
        r = cli.models.generate_content(
            model=chat_model(),
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
                http_options=types.HttpOptions(timeout=int(_CHAT_TIMEOUT * 1000)),
            ),
        )
        _record(True)
        return (getattr(r, "text", "") or "").strip() or UNAVAILABLE
    except Exception as exc:  # noqa: BLE001
        _record(False)
        print(f"[LLM:chat] {type(exc).__name__}: {exc}")
        return UNAVAILABLE


# ---------------------------------------------------------------- translate

_LANG = {"hi": "hindi", "te": "telugu"}
# spans kept verbatim: IS numbers (with year), URLs, emails, [ ... ] citations, CM/L codes
_PROTECT = re.compile(
    r"(IS\s?\d[\w./:\- ]{0,24}\d|https?://\S+|[\w.\-]+@[\w.\-]+|\[[^\]]+\]|CM/?L[\w\-/]*)", re.I)
_MARK_A, _MARK_B = "", ""

_TR_CACHE = {}
_TR_CACHE_MAX = 500


def _mask(text):
    keep = []
    def sub(m):
        keep.append(m.group(0))
        return f"{_MARK_A}{len(keep) - 1}{_MARK_B}"
    return _PROTECT.sub(sub, text), keep


def _unmask(text, keep):
    for i, v in enumerate(keep):
        for pat in (f"{_MARK_A}{i}{_MARK_B}", f"{_MARK_A} {i} {_MARK_B}",
                    f"{_MARK_A}{i} {_MARK_B}", f"{_MARK_A} {i}{_MARK_B}"):
            text = text.replace(pat, v)
    return text


def _deep_translate(text, lang):
    masked, keep = _mask(text)
    for _ in range(3):
        try:
            from deep_translator import GoogleTranslator
            out = GoogleTranslator(source="en", target=lang).translate(masked)
            if out:
                return _unmask(out, keep)
        except Exception:  # noqa: BLE001
            time.sleep(1.0)
    try:
        from deep_translator import MyMemoryTranslator
        out = MyMemoryTranslator(source="english", target=_LANG[lang]).translate(masked)
        if out:
            return _unmask(out, keep)
    except Exception:  # noqa: BLE001
        pass
    return None


def translate(text, target_lang):
    """Translate to hi/te (cached). Returns the original text if unavailable or
    target is English. IS numbers / URLs / citations preserved verbatim."""
    target_lang = (target_lang or "en").lower()
    if target_lang == "en" or target_lang not in _LANG or not (text or "").strip():
        return text
    ck = (target_lang, hash(text))
    if ck in _TR_CACHE:
        return _TR_CACHE[ck]

    # 1) keyless deep-translator, chunked on blank lines
    out, ok_any = [], False
    for ch in re.split(r"(\n{2,})", text):
        if ch.strip() and not ch.startswith("\n"):
            tr = _deep_translate(ch, target_lang)
            if tr:
                out.append(tr); ok_any = True; continue
        out.append(ch)
    result = "".join(out) if ok_any else None

    # 2) Gemini fallback
    if result is None:
        lang_name = _LANG[target_lang].title()
        got = chat(
            system=(f"Translate the user text into {lang_name}. Keep verbatim: Indian "
                    f"Standard numbers (e.g. IS 302-2-3), clause/section numbers, URLs, "
                    f"emails and text in square brackets. Output only the translation."),
            user=text, temperature=0.0, max_tokens=1200,
        )
        result = text if got == UNAVAILABLE else got

    if len(_TR_CACHE) >= _TR_CACHE_MAX:
        _TR_CACHE.clear()
    _TR_CACHE[ck] = result
    return result


# ---------------------------------------------------------------- startup

def check_connectivity():
    """One tiny call at startup. Returns (ok, detail)."""
    global _open_until
    if not _key():
        return False, "GEMINI_API_KEY not set - offline BIS engine + deep-translator"
    cli = _get_client()
    if cli is None:
        return False, f"Gemini client init failed ({_last_err})"
    try:
        from google.genai import types
        cli.models.generate_content(
            model=chat_model(), contents="ping",
            config=types.GenerateContentConfig(max_output_tokens=5))
        _record(True)
        return True, f"Gemini connected ({chat_model()})"
    except Exception as exc:  # noqa: BLE001
        _open_until = time.time() + _COOLDOWN
        return False, f"Gemini unreachable: {type(exc).__name__}: {str(exc)[:140]}"
