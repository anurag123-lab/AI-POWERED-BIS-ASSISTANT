"""
AI Orchestrator - the single entry point for "Ask Anything".

Flow:  message + product context  ->  detect_intent()  ->  route()  ->  action

    action = "navigate"     -> open a service page (Standards / Schemes / Testing
                               & Labs / Licensing / Documents) carrying the question
    action = "answer"       -> answer in place from the curated BIS knowledge base
                               (services.answer_engine); this is the real pipeline,
                               a full RAG index can be swapped in behind answer_engine
    action = "unsupported"  -> safe "not in the BIS knowledge base" response

Product context: the caller passes `product_id` (a compliance_cases.id). The
orchestrator resolves it to a product slug + location and never assumes a
single hard-coded product. Every turn is persisted to `search_history` keyed by
that case id, so conversations stay isolated per product.
"""

import json
import re

from database import get_db_connection
from services import knowledge_base as kb
from services import answer_engine

# ---------------------------------------------------------------- intent

# intent -> (service endpoint, url fragment, human label, kb area for answers)
SERVICES = {
    "standard":  ("standards",    "",       "Standards",       "standards"),
    "scheme":    ("schemes",      "",       "Schemes",         "scheme"),
    "licensing": ("licensing",    "",       "Licensing",       "licensing"),
    "testing":   ("testing_labs", "",       "Testing & Labs",  "testing"),
    "labs":      ("testing_labs", "#labs",  "Testing & Labs",  "supporting"),
    "documents": ("documents",    "",       "Documents",       "supporting"),
}

_INTENT_PATTERNS = [
    ("labs",      r"\b(lab|labs|laborator|nabl|where.*test|which.*lab|nearby lab|testing centre|testing center)\b"),
    ("testing",   r"\b(test|testing|test report|type test|routine test|sample|which tests|what tests)\b"),
    ("licensing", r"\b(licen[cs]e|licensing|apply|application|manak|portal|register(ed|ing|ation)?|how (do|to) i get|procedure|steps|timeline|cm/?l)\b"),
    ("scheme",    r"\b(scheme|isi mark|crs|compulsory registration|which scheme|scheme i|scheme ii|conformity assessment)\b"),
    ("documents", r"\b(document|documents|paperwork|checklist|what.*need to (prepare|submit)|sit\b|scheme of inspection)\b"),
    ("standard",  r"\b(standard|is\s?\d|is number|is code|specification|which is|applicable (is|standard))\b"),
]

_PRODUCT_INFO = r"\b(what('?s| is) my product|which product|my product context|current product|what am i working on)\b"
_OVERVIEW = r"\b(everything|overview|full picture|all (the )?(details|areas)|what do i need|get certified|end.to.end|summari[sz]e|compliance|certif)\b"

# The question must be on-topic (BIS / this product) before we answer or route.
_ON_TOPIC = re.compile(
    r"\b(bis|isi|is\s?\d|standard|scheme|crs|licen[cs]e|certif|qco|quality control|"
    r"test|testing|lab|laborator|mark|hallmark|huid|cm/?l|manak|clause|"
    r"compliance|comply|product|manufactur|import|export|document|require)\b", re.I)


def detect_intent(message, product_aliases=None):
    """Return one of: standard | scheme | licensing | testing | labs | documents
    | product_info | overview | unsupported."""
    m = (message or "").lower().strip()
    if not m:
        return "unsupported"
    if re.search(_PRODUCT_INFO, m):
        return "product_info"

    on_topic = bool(_ON_TOPIC.search(m)) or any(a in m for a in (product_aliases or []))

    if re.search(_OVERVIEW, m) and on_topic:
        return "overview"
    for intent, pat in _INTENT_PATTERNS:
        if re.search(pat, m):
            return intent
    if on_topic and ("?" in m or len(m.split()) >= 3):
        return "overview"
    return "unsupported"


# ---------------------------------------------------------------- context

def product_context(product_id):
    """Resolve a compliance_cases.id to the workspace product context."""
    if not product_id:
        return None
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM compliance_cases WHERE id = ?", (product_id,)).fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    c = dict(row)
    meta = kb.product_meta(c.get("product_slug")) or {}
    return {
        "product_id": c["id"],
        "slug": c.get("product_slug"),
        "name": meta.get("display_name") or c.get("product_name"),
        "is_number": meta.get("is_number") or c.get("is_number"),
        "scheme": meta.get("scheme"),
        "user_type": c.get("user_type"),
        "city": c.get("city"),
        "state": c.get("state"),
    }


# ---------------------------------------------------------------- routing

def route(intent):
    """intent -> {action, target_service, endpoint, url_fragment, label}."""
    if intent in SERVICES:
        ep, frag, label, _area = SERVICES[intent]
        return {"action": "navigate", "target_service": label,
                "endpoint": ep, "url_fragment": frag}
    if intent == "product_info":
        return {"action": "answer", "target_service": None}
    if intent == "overview":
        return {"action": "answer", "target_service": "Home"}
    return {"action": "unsupported", "target_service": None}


# ---------------------------------------------------------------- persist

def _persist(case_id, product_slug, message, intent, action, response_text, lang="en"):
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO search_history (user_id, case_id, product_slug, query, mode, answer_md, sources_json, area, language) "
            "VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, product_slug, message, f"{action}:{intent}", response_text or "",
             json.dumps([]), intent, lang),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[orchestrator] persist failed: {exc}")


def conversation(case_id, limit=30):
    """Per-product conversation history (isolated by case_id)."""
    if not case_id:
        return []
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, query, mode, area, answer_md, created_at FROM search_history "
        "WHERE case_id = ? ORDER BY id DESC LIMIT ?", (case_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- orchestrate

def orchestrate(product_id, message, current_service=None, language="en"):
    """
    Main entry point. Returns a dict the UI acts on:

      { intent, action, target_service, target_url, response,
        product: {id, name, ...}, mock: bool }
    """
    ctx = product_context(product_id)
    aliases = []
    if ctx and ctx.get("slug"):
        meta = kb.product_meta(ctx["slug"]) or {}
        aliases = [meta.get("display_name", "").lower()] + [a.lower() for a in meta.get("aliases", [])]
    intent = detect_intent(message, aliases)
    r = route(intent)
    action = r["action"]
    resp = {
        "intent": intent,
        "action": action,
        "target_service": r["target_service"],
        "target_url": None,
        "response": "",
        "product": ctx,
        "mock": False,
    }

    if ctx is None and action != "unsupported":
        resp["action"] = "unsupported"
        resp["response"] = ("No product workspace is active. Open a product from "
                            "My Cases first, then ask again.")
        return resp

    if action == "navigate":
        from flask import url_for
        try:
            base = url_for(r["endpoint"])
        except Exception:
            base = "/" + r["endpoint"].replace("_", "-")
        resp["target_url"] = f"{base}?ask={_q(message)}{r['url_fragment']}"
        resp["response"] = (f"That's a **{r['target_service']}** question for "
                            f"**{ctx['name']}**. Opening {r['target_service']}...")
        _persist(ctx["product_id"], ctx["slug"], message, intent, "navigate",
                 resp["response"], language)
        return resp

    if action == "answer" and intent == "product_info":
        resp["response"] = (
            f"Your active product is **{ctx['name']}**"
            + (f" ({ctx['is_number']})" if ctx.get("is_number") else "")
            + f". You are a **{ctx.get('user_type') or 'user'}** in "
            f"**{ctx.get('city') or '-'}, {ctx.get('state') or '-'}**. "
            f"Applicable scheme: **{ctx.get('scheme') or 'see Schemes'}**."
        )
        _persist(ctx["product_id"], ctx["slug"], message, intent, "answer",
                 resp["response"], language)
        return resp

    if action == "answer":  # overview -> real KB answer
        result = answer_engine.answer_question(ctx["slug"], message,
                                               location={"city": ctx["city"], "state": ctx["state"]},
                                               language=language)
        resp["mode"] = result.get("mode")
        resp["answers"] = result.get("answers")
        if result.get("mode") == "refused":
            resp["action"] = "unsupported"
            resp["response"] = (result.get("answer") or {}).get("body_md", "")
        else:
            first = (result.get("answers") or [{}])[0]
            resp["response"] = first.get("body_md", "")
            resp["target_service"] = "Home"
        _persist(ctx["product_id"], ctx.get("slug"), message, intent,
                 resp["action"], resp["response"], language)
        return resp

    # unsupported
    resp["response"] = (
        "I could not map that to a BIS service or find it in the available BIS "
        "knowledge base, so I will not guess. Try asking about your product's "
        "standard, scheme, testing, labs, licensing or documents"
        + (f" for **{ctx['name']}**." if ctx else ".")
    )
    if ctx:
        _persist(ctx["product_id"], ctx.get("slug"), message, intent, "unsupported",
                 resp["response"], language)
    return resp


def _q(s):
    from urllib.parse import quote
    return quote((s or "")[:200])
