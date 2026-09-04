"""
Curated BIS knowledge base loader.

Reads knowledge_base/_index.json + knowledge_base/<slug>.json (facts sourced
from bis.gov.in; every area carries source URLs). Provides product lookup,
free-text product matching, and per-area context flattening for the answer
engine.
"""

import json
import os
import threading

_KB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge_base")

# The 7 spec areas, in the order they appear on the personalised Home and PDF.
AREA_ORDER = [
    "standards",
    "certification",
    "scheme",
    "licensing",
    "testing",
    "related_standards",
    "supporting",
]

AREA_TITLES = {
    "standards": "Applicable Indian Standard",
    "certification": "Certification Requirement",
    "scheme": "BIS Scheme",
    "licensing": "Licensing Process",
    "testing": "Testing Requirements",
    "related_standards": "Related Standards",
    "supporting": "Documents & Recognised Laboratories",
}

# area -> Flask endpoint for the "View <feature>" button
AREA_ENDPOINT = {
    "standards": "standards",
    "certification": "schemes",
    "scheme": "schemes",
    "licensing": "licensing",
    "testing": "testing_labs",
    "related_standards": "standards",
    "supporting": "documents",
}

_lock = threading.Lock()
_index = None
_products = {}


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load(force=False):
    """Load and cache the index + all product files."""
    global _index, _products
    with _lock:
        if _index is not None and not force:
            return _index
        _index = _read_json(os.path.join(_KB_DIR, "_index.json"))
        _products = {}
        for entry in _index.get("products", []):
            slug = entry["slug"]
            try:
                _products[slug] = _read_json(os.path.join(_KB_DIR, f"{slug}.json"))
            except FileNotFoundError:
                print(f"[KB] missing file for {slug}")
        return _index


def list_products():
    load()
    return list(_index.get("products", []))


def get_product(slug):
    load()
    return _products.get(slug)


def product_meta(slug):
    load()
    for e in _index.get("products", []):
        if e["slug"] == slug:
            return e
    return None


def match_product(text):
    """Return a product slug for free text, or None if outside the KB."""
    if not text:
        return None
    load()
    t = " " + text.lower().strip() + " "
    # 1) exact slug or display-name
    for e in _index.get("products", []):
        if e["slug"] == text.lower().strip():
            return e["slug"]
    # 2) alias / display-name substring (longest alias first for specificity)
    best = None
    best_len = 0
    for e in _index.get("products", []):
        cands = [e["display_name"].lower()] + [a.lower() for a in e.get("aliases", [])]
        for c in cands:
            if c and (c in text.lower() or (" " + c + " ") in t) and len(c) > best_len:
                best, best_len = e["slug"], len(c)
    return best


def supported_names():
    load()
    return [e["display_name"] for e in _index.get("products", [])]


# --------------------------------------------------------------------------
# Area context — flatten one area of a product into (title, body_md, sources)
# --------------------------------------------------------------------------

def _collect_sources(*groups):
    seen, out = set(), []
    for g in groups:
        for s in (g or []):
            key = (s.get("doc"), s.get("url"))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "doc": s.get("doc"),
                "page": s.get("page"),
                "clause": s.get("clause"),
                "url": s.get("url"),
            })
    return out


def area_view(slug, area):
    """
    Return {title, body_md, sources, endpoint} for one of the 7 spec areas,
    built deterministically from the KB (this is the grounded 90%).
    Returns None if the product/area is not present.
    """
    p = get_product(slug)
    if not p:
        return None
    a = p.get("areas", {})
    title = AREA_TITLES.get(area, area.title())
    endpoint = AREA_ENDPOINT.get(area, "home")
    md, srcs = [], []

    if area == "standards":
        st = a.get("standards", {})
        prim = st.get("primary", {})
        md.append(f"**{prim.get('is_number','')}** - {prim.get('title','')}")
        if prim.get("summary"):
            md.append(prim["summary"])
        srcs = _collect_sources(prim.get("sources"))

    elif area == "related_standards":
        st = a.get("standards", {})
        rel = st.get("related", [])
        if not rel:
            return None
        for r in rel:
            md.append(f"- **{r.get('is_number','')}** - {r.get('title','')}  \n  _{r.get('relation','')}_")
        srcs = _collect_sources(*[r.get("sources") for r in rel])

    elif area == "certification":
        c = a.get("certification", {})
        md.append(("**Mandatory** - " if c.get("mandatory") else "") + (c.get("summary") or ""))
        qco = c.get("qco", {})
        if qco:
            md.append(
                f"**Quality Control Order:** {qco.get('title','')}  \n"
                f"Ministry: {qco.get('ministry','-')}  \n"
                f"Notified: {qco.get('notified','-')} | Enforced: {qco.get('enforced','-')}"
            )
        srcs = _collect_sources(c.get("sources"))

    elif area == "scheme":
        s = a.get("scheme", {})
        ap = s.get("applicable", {})
        md.append(f"**Applies: {ap.get('name','')}**")
        if ap.get("covers"):
            md.append(f"_Covers:_ {ap['covers']}")
        if ap.get("why"):
            md.append(f"_Why:_ {ap['why']}")
        if ap.get("you_receive"):
            md.append(f"_You receive:_ {ap['you_receive']}")
        na = s.get("not_applicable", [])
        if na:
            md.append("**Does NOT apply:**")
            for n in na:
                md.append(f"- **{n.get('name','')}** - {n.get('why','')}")
        srcs = _collect_sources(s.get("sources"))

    elif area == "licensing":
        lc = a.get("licensing", {})
        if lc.get("summary"):
            md.append(lc["summary"])
        for step in lc.get("steps", []):
            md.append(
                f"**Step {step.get('n','')}: {step.get('title','')}** "
                f"_(≈ {step.get('timeline','-')})_  \n{step.get('detail','')}"
            )
        srcs = _collect_sources(lc.get("sources"))

    elif area == "testing":
        ts = a.get("testing", {})
        if ts.get("summary"):
            md.append(ts["summary"])
        for t in ts.get("tests", []):
            cl = f" (clause {t['clause']})" if t.get("clause") else ""
            cond = f" - _{t['condition']}_" if t.get("condition") else ""
            md.append(f"- {t.get('name','')}{cl}{cond}")
        srcs = _collect_sources(ts.get("sources"))

    elif area == "supporting":
        docs = a.get("documents", {})
        labs = a.get("labs", {})
        if docs.get("summary"):
            md.append("**Documents to prepare**")
            md.append(docs["summary"])
        for it in docs.get("items", []):
            tag = (it.get("status") or "").upper()
            md.append(f"- **{it.get('name','')}** ({tag}) - {it.get('explanation','')}")
        if labs.get("entries"):
            md.append("\n**Recognised laboratories**")
            if labs.get("note"):
                md.append(labs["note"])
            for e in labs["entries"]:
                loc = ", ".join([x for x in [e.get("city"), e.get("state")] if x])
                md.append(f"- **{e.get('name','')}** - {loc} - {e.get('scope','')}")
        srcs = _collect_sources(
            *[it.get("sources") for it in docs.get("items", [])],
            labs.get("sources"),
            *[e.get("sources") for e in labs.get("entries", [])],
        )
    else:
        return None

    return {
        "area": area,
        "title": title,
        "body_md": "\n\n".join(m for m in md if m),
        "sources": srcs,
        "endpoint": endpoint,
    }


def area_context(slug, area):
    """Compact plain-text context block for the LLM (area body + source list)."""
    v = area_view(slug, area)
    if not v:
        return ""
    lines = [f"AREA: {v['title']}", v["body_md"], "", "SOURCES:"]
    for s in v["sources"]:
        bits = [s.get("doc") or ""]
        if s.get("page"):
            bits.append(f"p.{s['page']}")
        if s.get("clause"):
            bits.append(f"cl.{s['clause']}")
        if s.get("url"):
            bits.append(s["url"])
        lines.append(" | ".join(bits))
    return "\n".join(lines)
