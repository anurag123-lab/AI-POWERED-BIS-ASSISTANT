"""
Provider-agnostic LLM access point for the app.

This is the ONLY module the rest of the app imports for embeddings / chat /
translation. It tries providers in order of whichever API key is present:

    1. OpenAI   (OPENAI_API_KEY)   - chat + embeddings
    2. Gemini   (GEMINI_API_KEY)   - chat + embeddings   [free tier]
    -  offline  - local hashed embeddings; chat returns UNAVAILABLE

Translation (hi / te) goes through `deep-translator` first (no key needed),
then falls back to an LLM, then to the original English text. Indian Standard
numbers, URLs and bracketed citations are protected from translation.

Every call is wrapped: a missing key, quota error, or timeout never raises into
the request path. Each provider has its own short circuit-breaker so one dead
provider does not slow or disable the others.
"""

import os
import re
import time
import threading

_CHAT_TIMEOUT = 20.0
_EMBED_TIMEOUT = 8.0
_FAIL_LIMIT = 3
_COOLDOWN = 300.0

UNAVAILABLE = "__LLM_UNAVAILABLE__"
NOT_COVERED = "NOT_COVERED"

_lock = threading.Lock()
_state = {}          # provider -> {"client": obj, "fails": int, "open_until": float, "err": str}
_openai_client = None
_gemini_client = None


# ---------------------------------------------------------------- config

def _openai_key():
    return (os.getenv("OPENAI_API_KEY") or "").strip()


def _gemini_key():
    return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()


def openai_chat_model():
    return (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()


def openai_embed_model():
    return (os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small").strip()


def gemini_chat_model():
    return (os.getenv("GEMINI_MODEL") or "gemini-2.0-flash").strip()


def gemini_embed_model():
    return (os.getenv("GEMINI_EMBEDDING_MODEL") or "text-embedding-004").strip()


# ---------------------------------------------------------------- breaker

def _s(p):
    return _state.setdefault(p, {"fails": 0, "open_until": 0.0, "err": ""})


def _open(p):
    return time.time() < _s(p)["open_until"]


def _ok(p):
    st = _s(p)
    st["fails"] = 0
    st["open_until"] = 0.0


def _bad(p, err=""):
    st = _s(p)
    st["fails"] += 1
    st["err"] = str(err)[:200]
    if st["fails"] >= _FAIL_LIMIT:
        st["open_until"] = time.time() + _COOLDOWN
        print(f"[LLM] {p} circuit opened for {int(_COOLDOWN)}s")


# ---------------------------------------------------------------- clients

def _get_openai():
    global _openai_client
    if not _openai_key() or _open("openai"):
        return None
    if _openai_client is None:
        with _lock:
            if _openai_client is None:
                try:
                    from openai import OpenAI
                    _openai_client = OpenAI(api_key=_openai_key(), max_retries=0)
                except Exception as exc:  # noqa: BLE001
                    _s("openai")["err"] = f"{type(exc).__name__}: {exc}"
                    _openai_client = None
    return _openai_client


def _get_gemini():
    global _gemini_client
    if not _gemini_key() or _open("gemini"):
        return None
    if _gemini_client is None:
        with _lock:
            if _gemini_client is None:
                try:
                    from google import genai
                    _gemini_client = genai.Client(api_key=_gemini_key())
                except Exception as exc:  # noqa: BLE001
                    _s("gemini")["err"] = f"{type(exc).__name__}: {exc}"
                    _gemini_client = None
    return _gemini_client


def active_provider():
    if _get_openai():
        return "openai"
    if _get_gemini():
        return "gemini"
    return None


def llm_available():
    return active_provider() is not None


# ---------------------------------------------------------------- embeddings

def embed(text):
    """Return an embedding vector, or None (caller uses a local fallback)."""
    text = (text or "")[:8000]
    cli = _get_openai()
    if cli:
        try:
            r = cli.with_options(timeout=_EMBED_TIMEOUT).embeddings.create(
                model=openai_embed_model(), input=text)
            _ok("openai")
            return r.data[0].embedding
        except Exception as exc:  # noqa: BLE001
            _bad("openai", exc)
            print(f"[LLM:embed openai] {type(exc).__name__}: {exc}")
    cli = _get_gemini()
    if cli:
        try:
            r = cli.models.embed_content(model=gemini_embed_model(), contents=text)
            _ok("gemini")
            return list(r.embeddings[0].values)
        except Exception as exc:  # noqa: BLE001
            _bad("gemini", exc)
            print(f"[LLM:embed gemini] {type(exc).__name__}: {exc}")
    return None


# ---------------------------------------------------------------- chat

def chat(system, user, *, temperature=0.2, max_tokens=700):
    """Single completion. Returns text, or UNAVAILABLE on any failure."""
    cli = _get_openai()
    if cli:
        try:
            r = cli.with_options(timeout=_CHAT_TIMEOUT).chat.completions.create(
                model=openai_chat_model(), temperature=temperature, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}])
            _ok("openai")
            return (r.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            _bad("openai", exc)
            print(f"[LLM:chat openai] {type(exc).__name__}: {exc}")

    cli = _get_gemini()
    if cli:
        try:
            from google.genai import types
            r = cli.models.generate_content(
                model=gemini_chat_model(),
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            _ok("gemini")
            return (getattr(r, "text", "") or "").strip() or UNAVAILABLE
        except Exception as exc:  # noqa: BLE001
            _bad("gemini", exc)
            print(f"[LLM:chat gemini] {type(exc).__name__}: {exc}")

    return UNAVAILABLE


# ---------------------------------------------------------------- translate

_LANG = {"hi": "hindi", "te": "telugu"}
# spans to keep verbatim: IS numbers, URLs, emails, [ ... ] citations, CM/L codes
_PROTECT = re.compile(
    r"(IS\s?\d[\w./:\- ]*?\d|https?://\S+|[\w.\-]+@[\w.\-]+|\[[^\]]+\]|CM/?L[\w\-/]*)", re.I)


def _mask(text):
    keep = []
    def sub(m):
        keep.append(m.group(0))
        return f"{len(keep) - 1}"
    return _PROTECT.sub(sub, text), keep


def _unmask(text, keep):
    for i, v in enumerate(keep):
        text = text.replace(f"{i}", v)
    return text


def _deep_translate(text, lang):
    masked, keep = _mask(text)
    for attempt in range(3):
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


_TR_CACHE = {}
_TR_CACHE_MAX = 500


def translate(text, target_lang):
    """Translate to hi/te. Returns the original text unchanged if translation
    is unavailable or the target is English. IS numbers / URLs / citations are
    preserved verbatim. Results are cached in-process."""
    target_lang = (target_lang or "en").lower()
    if target_lang == "en" or target_lang not in _LANG or not (text or "").strip():
        return text

    ck = (target_lang, hash(text))
    if ck in _TR_CACHE:
        return _TR_CACHE[ck]
    result = _translate_uncached(text, target_lang)
    if len(_TR_CACHE) >= _TR_CACHE_MAX:
        _TR_CACHE.clear()
    _TR_CACHE[ck] = result
    return result


def _translate_uncached(text, target_lang):

    # 1) keyless deep-translator, chunked by blank line to stay under limits
    chunks = re.split(r"(\n{2,})", text)
    out, ok_any = [], False
    for ch in chunks:
        if ch.strip() and not ch.startswith("\n"):
            tr = _deep_translate(ch, target_lang)
            if tr:
                out.append(tr)
                ok_any = True
                continue
        out.append(ch)
    if ok_any:
        return "".join(out)

    # 2) LLM fallback
    lang_name = _LANG[target_lang].title()
    got = chat(
        system=(f"Translate the user text into {lang_name}. Keep verbatim: Indian "
                f"Standard numbers (e.g. IS 302-2-3), clause/section numbers, URLs, "
                f"emails, and text in square brackets. Output only the translation."),
        user=text, temperature=0.0, max_tokens=1200,
    )
    return text if got == UNAVAILABLE else got


# ---------------------------------------------------------------- startup

def check_connectivity():
    """One tiny call at startup. Returns (ok, detail)."""
    if not _openai_key() and not _gemini_key():
        return False, "no LLM key (OPENAI_API_KEY / GEMINI_API_KEY) - offline engine + deep-translator"

    cli = _get_openai()
    if cli:
        try:
            cli.with_options(timeout=_EMBED_TIMEOUT).embeddings.create(
                model=openai_embed_model(), input="ping")
            _ok("openai")
            return True, f"OpenAI connected ({openai_chat_model()})"
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            _s("openai")["open_until"] = time.time() + _COOLDOWN
            if "insufficient_quota" in msg or "credit balance" in msg.lower():
                # fall through to try Gemini
                pass

    cli = _get_gemini()
    if cli:
        try:
            from google.genai import types
            r = cli.models.generate_content(
                model=gemini_chat_model(), contents="ping",
                config=types.GenerateContentConfig(max_output_tokens=5))
            _ok("gemini")
            return True, f"Gemini connected ({gemini_chat_model()})"
        except Exception as exc:  # noqa: BLE001
            _s("gemini")["open_until"] = time.time() + _COOLDOWN
            return False, f"Gemini error: {type(exc).__name__}: {str(exc)[:120]}"

    return False, "LLM key present but unreachable - offline engine + deep-translator"
