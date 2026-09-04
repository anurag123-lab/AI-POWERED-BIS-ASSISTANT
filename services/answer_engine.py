"""
Answer engine — turns a product + question into grounded, cited answers.

- ~90% comes from the curated BIS knowledge base (services/knowledge_base.py),
  rendered deterministically. Always available.
- ~10% is OpenAI (services/llm.py): it tightens phrasing and answers the
  specific question strictly from the KB context. If the model is unavailable
  or says NOT_COVERED, the deterministic text stands.
- Off-topic / no-product questions get a measured refusal and are logged to
  audit_logs for the Documentation Gap Report (spec section 17-18).
"""

import json
import re

from database import get_db_connection
from services import knowledge_base as kb
from services import llm
from services.rag_engine import fanout_7_searches, MEASURED_REFUSAL_THRESHOLD

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
    "manufacturers, importers and consumers. Answer ONLY from the CONTEXT block. "
    "Do not add facts that are not in the context. Keep Indian Standard numbers "
    "(e.g. IS 302-2-3), clause numbers and URLs exactly as given. Be concise and "
    "practical. If the context does not contain enough to answer, reply with the "
    "single token NOT_COVERED."
)


# --------------------------------------------------------------------------

def _refine_with_llm(question, context, deterministic_md):
    """Return an LLM-tightened answer, or the deterministic text on any miss."""
    if not llm.llm_available():
        return deterministic_md, False
    user = (
        f"QUESTION: {question or 'Give a clear summary of this area for my product.'}\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"Write the answer in Markdown. End with nothing extra."
    )
    out = llm.chat(_LLM_SYSTEM, user, temperature=0.15, max_tokens=650)
    if out == llm.UNAVAILABLE or not out.strip():
        return deterministic_md, False
    if out.strip().upper().startswith(llm.NOT_COVERED):
        return deterministic_md, False
    return out.strip(), True


def _maybe_translate(text, language):
    if language and language != "en":
        return llm.translate(text, language)
    return text


def answer_area(slug, area, question=None, language="en"):
    """One area answer: {area,title,body_md,sources,feature_endpoint,grounded,refused,llm_used}."""
    view = kb.area_view(slug, area)
    if not view:
        return {
            "area": area, "title": kb.AREA_TITLES.get(area, area),
            "body_md": "This area is not covered for the selected product in the BIS knowledge base.",
            "sources": [], "feature_endpoint": kb.AREA_ENDPOINT.get(area, "home"),
            "grounded": False, "refused": True, "llm_used": False,
        }
    body, llm_used = _refine_with_llm(question, kb.area_context(slug, area), view["body_md"])
    body = _maybe_translate(body, language)
    return {
        "area": area,
        "title": view["title"],
        "body_md": body,
        "sources": view["sources"],
        "feature_endpoint": view["endpoint"],
        "grounded": True,
        "refused": False,
        "llm_used": llm_used,
    }


def answer_seven(slug, location=None, language="en"):
    """The 7 area answers in spec order (skips related_standards only if the KB has none)."""
    out = []
    for area in SEVEN_AREAS:
        a = answer_area(slug, area, question=None, language=language)
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
    q = (question or "").lower()
    return any(h in q for h in _BROAD_HINTS) or len(q.split()) <= 4


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


def answer_question(slug, question, location=None, language="en"):
    """
    Intent-routed answer.
      mode 'seven'   -> broad certification/compliance question, returns 7 cards
      mode 'area'    -> narrow question, returns one (or few) area answers
      mode 'refused' -> outside the BIS knowledge base, logged as a gap
    """
    question = (question or "").strip()

    # No product context and none inferable -> measured refusal (spec 18/20).
    if not slug:
        slug = kb.match_product(question)
    if not slug:
        chunks = fanout_7_searches(question) if question else []
        max_score = max((c["relevance_score"] for c in chunks), default=0.0)
        _log_gap(question, None, _topic_of(question), max_score)
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

    if _is_broad(question) or not areas:
        return {
            "mode": "seven",
            "product": slug,
            "product_name": meta.get("display_name", slug),
            "language": language,
            "answers": answer_seven(slug, location, language),
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
