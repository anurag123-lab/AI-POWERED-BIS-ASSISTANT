"""
Answer engine — turns a product + question into grounded, cited answers.

- ~70% is grounded BIS content: the curated knowledge base + verbatim
  passages from the ingested BIS Product Manual PDFs. Always available.
- ~30% is Google Gemini (services/llm.py): within that BIS context only, it
  rephrases, connects and answers the specific question, citing each fact.
  If the model is unavailable / says NOT_COVERED, the 100%-BIS text stands.
- Off-topic / no-product questions get a measured refusal and are logged to
  audit_logs for the Documentation Gap Report (spec section 17-18).
"""

import json
import re

from database import get_db_connection
from services import knowledge_base as kb
from services import llm
from services.rag_engine import fanout_7_searches, perform_hybrid_search, MEASURED_REFUSAL_THRESHOLD

# area -> retrieval query for pulling a verbatim passage from the ingested BIS PDFs
_AREA_QUERY = {
    "standards": "scope specification requirements of this standard",
    "related_standards": "normative references other standards referred",
    "certification": "compulsory quality control order standard mark marking",
    "scheme": "scheme of inspection and testing conformity assessment licence",
    "licensing": "grant of licence application procedure fee",
    "testing": "type test routine test acceptance test sampling",
    "supporting": "documents required application scheme of inspection and testing",
}

_HAS_CHUNKS = {}


def _product_has_chunks(slug):
    if slug in _HAS_CHUNKS:
        return _HAS_CHUNKS[slug]
    try:
        conn = get_db_connection()
        n = conn.execute("SELECT COUNT(*) c FROM document_chunks WHERE product_slug = ?",
                         (slug,)).fetchone()["c"]
        conn.close()
    except Exception:
        n = 0
    _HAS_CHUNKS[slug] = n > 0
    return n > 0


def verbatim_excerpts(slug, area, question=None, limit=2, fast=False):
    """Top matching passages from the product's ingested BIS PDFs (or []).
    fast=True -> BM25 only, no network embedding call."""
    if not slug or not _product_has_chunks(slug):
        return []
    q = (question or "").strip() or _AREA_QUERY.get(area, area)
    try:
        hits = perform_hybrid_search(q, top_k=limit, product_slug=slug,
                                     use_embedding=not fast)
    except Exception:
        return []
    out = []
    for h in hits:
        if h.get("relevance_score", 0) < 0.45:
            continue
        text = (h.get("content") or "").strip()
        if len(text) > 480:
            text = text[:480].rsplit(" ", 1)[0] + "…"
        out.append({
            "text": text,
            "page": h.get("page_number"),
            "doc": h.get("doc_title") or h.get("doc_code"),
            "url": h.get("doc_url") or h.get("url"),
            "score": round(h.get("relevance_score", 0), 3),
        })
    return out

SEVEN_AREAS = kb.AREA_ORDER  # standards, certification, scheme, licensing, testing, related_standards, supporting

# keyword -> area, for routing a narrow question
_AREA_KEYWORDS = {
    "standards": ["standard", "is number", "is code", "specification", "which is", "applicable is"],
    "related_standards": ["related standard", "other standard", "connected standard", "referenced standard"],
    "certification": ["certification", "certificate", "mandatory", "compulsory", "qco", "quality control order", "do i need", "required to"],
    "scheme": ["scheme", "isi mark", "crs", "registration scheme", "scheme i", "scheme ii", "which scheme"],
    "licensing": ["licence", "license", "licensing", "apply", "application", "process", "steps", "timeline", "how long", "manak", "portal"],
    "testing": ["test", "testing", "type test", "routine test", "sample", "lab test"],
    "supporting": ["document", "documents", "paperwork", "checklist", "lab", "laboratory", "labs", "where can i test", "which lab"],
}

_BROAD_HINTS = [
    "everything", "all", "overview", "what do i need", "how do i get", "get certified",
    "get bis", "get certification", "comply", "compliance", "start", "begin", "full process",
    "end to end", "roadmap",
]

_LLM_SYSTEM = (
    "You are a BIS (Bureau of Indian Standards) compliance assistant for Indian "
    "manufacturers, importers and consumers.\n"
    "SOURCE RULE: answer ONLY from the BIS CONTEXT block. It is drawn from "
    "bis.gov.in pages, Government of India notifications and BIS Product Manual "
    "PDFs.\n"
    "BLEND RULE: about 70% of your answer must be facts, figures, dates, clause "
    "numbers and short quoted phrases taken straight from the CONTEXT, each "
    "followed by a [doc p.X] or [doc] citation. The remaining ~30% may be your "
    "own plain-language connective explanation and practical framing. Never "
    "introduce a fact, number, standard, scheme or timeline that is not in the "
    "CONTEXT.\n"
    "Keep Indian Standard numbers (e.g. IS 302-2-3), clause/section numbers and "
    "URLs exactly as given. Be concise. If the CONTEXT does not contain enough "
    "to answer, reply with the single token NOT_COVERED."
)

BLEND_GROUNDED = {"bis": 100, "ai": 0}
BLEND_SYNTH = {"bis": 70, "ai": 30}


# --------------------------------------------------------------------------

def _compose_bis_context(slug, area, question):
    """The '70%': KB area facts + verbatim passages from the ingested BIS PDFs."""
    parts = [kb.area_context(slug, area)[:2200]]
    for ex in verbatim_excerpts(slug, area, question, limit=2):
        cite = f"{ex.get('doc', 'BIS document')}"
        if ex.get("page"):
            cite += f" p.{ex['page']}"
        parts.append(f'VERBATIM [{cite}]:\n"{ex.get("text", "")[:340]}"')
    return "\n\n".join(p for p in parts if p)


def _synthesize(slug, area, question, deterministic_md):
    """Return (body_md, ai_used, blend). ~70% BIS context / ~30% AI framing when
    a model is reachable; otherwise the 100%-deterministic KB body."""
    if not llm.llm_available():
        return deterministic_md, False, BLEND_GROUNDED
    ctx = _compose_bis_context(slug, area, question)
    user = (
        f"QUESTION: {question or 'Summarise this area for my product, for someone new to BIS.'}\n\n"
        f"BIS CONTEXT:\n{ctx}\n\n"
        f"Write the answer in Markdown, ~70% direct-from-context with [citations], "
        f"~30% your own connective explanation. Nothing after the answer."
    )
    out = llm.chat(_LLM_SYSTEM, user, temperature=0.15, max_tokens=550)
    if out == llm.UNAVAILABLE or not out.strip():
        return deterministic_md, False, BLEND_GROUNDED
    if out.strip().upper().startswith(llm.NOT_COVERED):
        return deterministic_md, False, BLEND_GROUNDED
    return out.strip(), True, BLEND_SYNTH


def _maybe_translate(text, language):
    if language and language != "en":
        return llm.translate(text, language)
    return text


# In-process cache for the LLM-synthesised area bodies. The BIS context per
# (product, area, question) is static, so a synthesised answer is stable; this
# keeps repeat page loads instant and free-tier quota use low.
_AREA_CACHE = {}
_AREA_CACHE_MAX = 400


def answer_area(slug, area, question=None, language="en", use_llm=True):
    """One area answer. use_llm=False -> fast 100%-BIS deterministic body
    (used for the 7-card Home); use_llm=True -> 70% BIS / 30% AI (feature pages,
    chatbot), cached per (slug, area, question, lang)."""
    view = kb.area_view(slug, area)
    if not view:
        return {
            "area": area, "title": kb.AREA_TITLES.get(area, area),
            "body_md": "This area is not covered for the selected product in the BIS knowledge base.",
            "sources": [], "feature_endpoint": kb.AREA_ENDPOINT.get(area, "home"),
            "excerpts": [], "grounded": False, "refused": True, "llm_used": False,
            "blend": BLEND_GROUNDED,
        }

    excerpts = verbatim_excerpts(slug, area, question, fast=not use_llm)

    if use_llm and llm.llm_available():
        ck = (slug, area, (question or "").strip().lower(), language)
        if ck in _AREA_CACHE:
            body, llm_used, blend = _AREA_CACHE[ck]
        else:
            body, llm_used, blend = _synthesize(slug, area, question, view["body_md"])
            body = _maybe_translate(body, language)
            if len(_AREA_CACHE) >= _AREA_CACHE_MAX:
                _AREA_CACHE.clear()
            _AREA_CACHE[ck] = (body, llm_used, blend)
    else:
        body = _maybe_translate(view["body_md"], language)
        llm_used, blend = False, BLEND_GROUNDED

    return {
        "area": area,
        "title": view["title"],
        "body_md": body,
        "sources": view["sources"],
        "excerpts": excerpts,
        "feature_endpoint": view["endpoint"],
        "grounded": True,
        "refused": False,
        "llm_used": llm_used,
        "blend": blend,
    }


def answer_seven(slug, location=None, language="en", use_llm=False):
    """The 7 area answers in spec order. Deterministic (fast) by default so the
    Home page renders instantly; the AI 30% is applied when a user opens a
    feature page or asks the assistant a specific question."""
    out = []
    for area in SEVEN_AREAS:
        a = answer_area(slug, area, question=None, language=language, use_llm=use_llm)
        if a["refused"] and area == "related_standards":
            continue
        out.append(a)
    return out


def _route_area(question):
    q = (question or "").lower()
    hits = []
    for area, kws in _AREA_KEYWORDS.items():
        if any(k in q for k in kws):
            hits.append(area)
    return hits


def _is_broad(question):
    q = " " + (question or "").lower().strip() + " "
    if len(q.split()) <= 3:
        return True
    for h in _BROAD_HINTS:
        # word-boundary match so "all" doesn't fire inside "small"
        if re.search(r"(?<![a-z])" + re.escape(h) + r"(?![a-z])", q):
            return True
    return False


def _log_gap(query, product_slug, category, max_score):
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO audit_logs (user_id, action_type, details) VALUES (?, ?, ?)",
            (None, "DOCUMENTATION_GAP_REFUSAL", json.dumps({
                "query": query, "product": product_slug, "category": category,
                "max_score": round(max_score, 3),
            })),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[answer_engine] gap-log failed: {exc}")


def _topic_of(question):
    q = (question or "").lower()
    for kw in ["cost", "fee", "price", "time", "timeline", "duration", "validity",
               "renewal", "penalty", "export", "import", "subsidy", "msme"]:
        if kw in q:
            return kw
    return (q.split()[0] if q.split() else "unknown")


_GENERAL_SYSTEM = (
    "You are a knowledgeable assistant for Indian manufacturers, importers and "
    "consumers dealing with BIS (Bureau of Indian Standards) certification. The "
    "user is currently working on the product: {product}. The curated BIS "
    "knowledge base did not cover this question, so you MAY answer from general "
    "knowledge and industry practice — but ONLY if the question is about BIS, "
    "Indian Standards, this (or any) product's technical specifications, "
    "certification, testing, licensing or compliance. If the question is "
    "unrelated to any of that (e.g. general chit-chat, pricing/market "
    "questions, weather, or any other off-topic subject), reply with the "
    "single token NOT_COVERED and nothing else. Otherwise answer clearly and "
    "practically. Begin the reply with the line: _(General guidance - not "
    "quoted from an official BIS document. Verify specifics on bis.gov.in.)_ "
    "Keep Indian Standard numbers exact. Be concise (a short paragraph or a "
    "few bullets)."
)

_OUT_OF_SCOPE_MSG = (
    "This query is outside the scope of the BIS Assistant. Please ask a BIS, "
    "product, standards, technical, or compliance-related question."
)


def general_answer(question, product_name=None, language="en"):
    """A normal-chatbot answer for questions the BIS knowledge base does not
    cover. Used only by the Home assistant (allow_general=True). Refuses with
    a scope message for anything unrelated to BIS/standards/compliance."""
    if not llm.llm_available():
        return {
            "title": "Not in the BIS knowledge base",
            "body_md": _maybe_translate(
                "I could not find this in the available BIS sources, and the AI "
                "assistant is offline right now, so I will not guess. Try asking "
                "about your product's standard, scheme, testing, labs, licensing "
                "or documents.", language),
            "sources": [], "general": True,
        }
    out = llm.chat(_GENERAL_SYSTEM.format(product=product_name or "a BIS-regulated product"),
                   question, temperature=0.3, max_tokens=550)
    if (out == llm.UNAVAILABLE or not out.strip()
            or out.strip().upper().startswith(llm.NOT_COVERED)):
        return {
            "title": "Outside the BIS Assistant's scope",
            "body_md": _maybe_translate(_OUT_OF_SCOPE_MSG, language),
            "sources": [], "general": True,
        }
    return {
        "title": "Assistant",
        "body_md": _maybe_translate(out.strip(), language),
        "sources": [], "general": True,
    }


def answer_question(slug, question, location=None, language="en", allow_general=False):
    """
    Intent-routed answer.
      mode 'seven'   -> broad certification/compliance question, returns 7 cards
      mode 'area'    -> narrow question, returns one (or few) area answers
      mode 'general' -> not in the BIS KB; Home assistant answers from general
                        knowledge (only when allow_general=True)
      mode 'refused' -> outside the BIS knowledge base, logged as a gap
    """
    question = (question or "").strip()

    # No product context and none inferable.
    if not slug:
        slug = kb.match_product(question)
    if not slug:
        chunks = fanout_7_searches(question) if question else []
        max_score = max((c["relevance_score"] for c in chunks), default=0.0)
        _log_gap(question, None, _topic_of(question), max_score)
        if allow_general:
            return {
                "mode": "general", "product": None, "language": language,
                "answer": general_answer(question, None, language),
                "grounding_score": round(max_score, 3),
            }
        return {
            "mode": "refused",
            "product": None,
            "language": language,
            "answer": {
                "title": "Not in the BIS knowledge base",
                "body_md": _maybe_translate(
                    "I could not find reliable information about this in the "
                    "available BIS sources for a known product, so I will not guess.\n\n"
                    "Currently supported products: "
                    + ", ".join(kb.supported_names())
                    + ".\n\nStart a product from **My Cases -> Start Another Product** "
                    "or ask again naming one of these products.",
                    language,
                ),
                "sources": [],
            },
            "grounding_score": round(max_score, 3),
            "refusal_threshold": MEASURED_REFUSAL_THRESHOLD,
        }

    meta = kb.product_meta(slug) or {}
    areas = _route_area(question)

    if _is_broad(question):
        return {
            "mode": "seven",
            "product": slug,
            "product_name": meta.get("display_name", slug),
            "language": language,
            "answers": answer_seven(slug, location, language, use_llm=True),
        }

    if not areas:
        # A specific question that doesn't map to any of the 7 areas.
        if allow_general:
            return {
                "mode": "general", "product": slug,
                "product_name": meta.get("display_name", slug), "language": language,
                "answer": general_answer(question, meta.get("display_name", slug), language),
            }
        return {
            "mode": "seven",
            "product": slug,
            "product_name": meta.get("display_name", slug),
            "language": language,
            "answers": answer_seven(slug, location, language, use_llm=True),
        }

    picked = [answer_area(slug, a, question=question, language=language) for a in areas[:3]]
    return {
        "mode": "area",
        "product": slug,
        "product_name": meta.get("display_name", slug),
        "language": language,
        "answers": picked,
        "answer": picked[0],
    }
