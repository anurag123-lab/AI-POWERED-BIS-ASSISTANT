"""
Ingest BIS PDF documents (Product Manuals, gazette notifications) into
document_chunks so answers can quote verbatim clause text with a page number.

For each product in knowledge_base/, collects the .pdf URLs referenced in its
areas, downloads + caches them (services.bis_fetch), extracts text per page
with pdfplumber, splits into ~900-char passages, embeds each (services.llm.embed
-> local hashed fallback), and stores them in `documents` + `document_chunks`
tagged with product_slug.

    python tools/ingest_pdfs.py [slug ...]     # default: all products

Idempotent per (product_slug, source_url): re-running replaces that document's
chunks. Best-effort - a PDF that 404s or won't parse is skipped with a note.
"""

import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from database import get_db_connection, init_db  # noqa: E402
from services import bis_fetch  # noqa: E402
from services import llm  # noqa: E402
from services import knowledge_base as kb  # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "knowledge_base", "_cache")
CHUNK_CHARS = 900
CHUNK_OVERLAP = 120


_SKIP_URL = re.compile(r"group[_-]?\d|/labs/|LRS_|Organisation-Chart", re.I)

# Areas whose PDFs are product-specific standard/manual text worth quoting.
_INGEST_AREAS = ("standards", "certification", "scheme", "testing", "licensing", "documents")


def _pdf_urls(product):
    urls = {}
    def walk(o):
        if isinstance(o, dict):
            u = o.get("url")
            if (isinstance(u, str) and u.lower().split("?")[0].endswith(".pdf")
                    and not _SKIP_URL.search(u)):
                urls[u] = o.get("doc") or u.rsplit("/", 1)[-1]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    areas = product.get("areas", {})
    for a in _INGEST_AREAS:
        walk(areas.get(a))
    return urls


def _download(url):
    os.makedirs(CACHE, exist_ok=True)
    fn = os.path.join(CACHE, re.sub(r"[^A-Za-z0-9._-]", "_", url)[-120:])
    if os.path.exists(fn) and os.path.getsize(fn) > 2000:
        return open(fn, "rb").read()
    try:
        import requests
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=45)
        r.raise_for_status()
        if b"%PDF" not in r.content[:1024]:
            return None
        open(fn, "wb").write(r.content)
        return r.content
    except Exception as exc:  # noqa: BLE001
        print(f"      download failed: {exc}")
        return None


def _pages(pdf_bytes):
    import pdfplumber
    out = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            txt = (pg.extract_text() or "").strip()
            if txt:
                out.append((i, re.sub(r"[ \t]+", " ", txt)))
    return out


def _chunk(text):
    text = text.strip()
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = start + CHUNK_CHARS
        cut = text.rfind(". ", start + 400, end)
        if cut == -1 or cut <= start:
            cut = end
        chunks.append(text[start:cut].strip())
        start = max(cut - CHUNK_OVERLAP, start + 1)
    return [c for c in chunks if len(c) > 60]


def _section_of(text):
    m = re.search(r"(clause|cl\.?|section)\s*([0-9]+(?:\.[0-9]+)*)", text, re.I)
    return f"Clause {m.group(2)}" if m else ""


def ingest_product(slug):
    prod = kb.get_product(slug)
    if not prod:
        print(f"  {slug}: not in KB, skip")
        return 0
    meta = kb.product_meta(slug) or {}
    urls = _pdf_urls(prod)
    if not urls:
        print(f"  {slug}: no PDF sources")
        return 0

    conn = get_db_connection()
    cur = conn.cursor()
    total = 0
    for url, title in urls.items():
        print(f"  {slug} <- {title}")
        data = _download(url)
        if not data:
            continue
        try:
            pages = _pages(data)
        except Exception as exc:  # noqa: BLE001
            print(f"      parse failed: {exc}")
            continue
        if not pages:
            print("      no extractable text (scanned PDF?)")
            continue

        # replace any prior chunks for this (slug, url)
        cur.execute("DELETE FROM document_chunks WHERE product_slug = ? AND source_url = ?", (slug, url))
        cur.execute("DELETE FROM documents WHERE product_slug = ? AND source_url = ?", (slug, url))
        cur.execute(
            "INSERT INTO documents (doc_code, title, category, url, source_url, product_slug, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (meta.get("is_number", slug), title, meta.get("category", ""), url, url, slug),
        )
        doc_id = cur.lastrowid

        n = 0
        for page_no, ptext in pages:
            for ch in _chunk(ptext):
                emb = llm.embed(ch) or []
                cur.execute(
                    "INSERT INTO document_chunks "
                    "(document_id, doc_code, doc_title, page_number, section_heading, content, embedding_json, product_slug, source_url) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (doc_id, meta.get("is_number", slug), title, page_no, _section_of(ch),
                     ch, json.dumps(emb), slug, url),
                )
                n += 1
        conn.commit()
        print(f"      {len(pages)} pages -> {n} chunks")
        total += n

    conn.close()
    return total


def main():
    init_db()
    slugs = sys.argv[1:] or [p["slug"] for p in kb.list_products()]
    print(f"OpenAI embeddings: {'on' if llm.llm_available() else 'OFF (local hashed vectors)'}")
    grand = 0
    for s in slugs:
        grand += ingest_product(s)
    print(f"\nDone. {grand} chunks ingested for {len(slugs)} product(s).")


if __name__ == "__main__":
    main()
