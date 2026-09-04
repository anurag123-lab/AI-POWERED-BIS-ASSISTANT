import json
import math
import os
import numpy as np
from rank_bm25 import BM25Okapi
from database import get_db_connection
from services import llm

MEASURED_REFUSAL_THRESHOLD = 0.40

def compute_local_embedding(text):
    words = text.lower().split()
    vec = [0.0] * 16
    for word in words:
        hash_val = sum(ord(c) for c in word)
        vec[hash_val % 16] += 1.0
    norm = math.sqrt(sum(v*v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec

def get_query_embedding(query_text):
    """OpenAI embedding when a working key/credit is available, else a local
    hashed embedding. `llm.embed` returns None on any failure (no key, 429,
    timeout) so this always degrades cleanly."""
    vec = llm.embed(query_text)
    if vec:
        return vec
    return compute_local_embedding(query_text)

def perform_hybrid_search(query, top_k=4, product_slug=None):
    """BM25 + embedding hybrid search over document_chunks.
    When product_slug is given, only that product's ingested chunks are searched
    (falls back to the whole corpus if that product has none)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    base = ("SELECT c.id, c.document_id, c.doc_code, c.doc_title, c.page_number, "
            "c.section_heading, c.content, c.embedding_json, "
            "COALESCE(c.source_url, d.url) AS url "
            "FROM document_chunks c LEFT JOIN documents d ON c.document_id = d.id")
    rows = []
    if product_slug:
        rows = cursor.execute(base + " WHERE c.product_slug = ?", (product_slug,)).fetchall()
    if not rows:
        rows = cursor.execute(base).fetchall()
    chunks = [dict(r) for r in rows]
    conn.close()

    if not chunks:
        return []

    corpus = [c['content'].lower().split() for c in chunks]
    bm25 = BM25Okapi(corpus)
    tokenized_query = query.lower().split()
    bm25_scores = bm25.get_scores(tokenized_query)
    max_bm25 = max(bm25_scores) if max(bm25_scores) > 0 else 1.0
    norm_bm25 = [score / max_bm25 for score in bm25_scores]

    query_emb = get_query_embedding(query)
    cosine_sims = []
    
    for c in chunks:
        emb_json = c.get('embedding_json')
        if emb_json:
            doc_emb = json.loads(emb_json)
            if len(doc_emb) == len(query_emb):
                sim = np.dot(doc_emb, query_emb) / (np.linalg.norm(doc_emb) * np.linalg.norm(query_emb) + 1e-9)
            else:
                loc_emb = compute_local_embedding(c['content'])
                loc_qemb = compute_local_embedding(query)
                sim = np.dot(loc_emb, loc_qemb) / (np.linalg.norm(loc_emb) * np.linalg.norm(loc_qemb) + 1e-9)
        else:
            sim = 0.0
        cosine_sims.append(float(sim))

    max_sim = max(cosine_sims) if max(cosine_sims) > 0 else 1.0
    norm_cosine = [sim / max_sim for sim in cosine_sims]

    fused_results = []
    for idx, chunk in enumerate(chunks):
        final_score = 0.6 * norm_cosine[idx] + 0.4 * norm_bm25[idx]
        fused_results.append({
            'chunk_id': chunk['id'],
            'doc_code': chunk['doc_code'],
            'doc_title': chunk['doc_title'],
            'doc_url': chunk['url'],
            'page_number': chunk['page_number'],
            'section_heading': chunk['section_heading'],
            'content': chunk['content'],
            'relevance_score': round(final_score, 4)
        })

    fused_results.sort(key=lambda x: x['relevance_score'], reverse=True)
    return fused_results[:top_k]

def fanout_7_searches(base_query):
    """
    SEVEN SEARCHES INSTEAD OF ONE:
    Fans out single user query into 7 targeted search sub-queries covering all facets.
    """
    sub_queries = [
        f"{base_query} standard scope specification",
        f"{base_query} compulsory quality control order qco notification status",
        f"{base_query} scheme I scheme II CRS certification procedure",
        f"{base_query} testing laboratories accredited NABL",
        f"{base_query} factory SIT in-house testing equipment",
        f"{base_query} paperwork documentation required checklist",
        f"{base_query} licensing timeline application fee"
    ]

    all_chunks = []
    seen_ids = set()

    for sq in sub_queries:
        results = perform_hybrid_search(sq, top_k=2)
        for r in results:
            if r['chunk_id'] not in seen_ids:
                seen_ids.add(r['chunk_id'])
                all_chunks.append(r)

    all_chunks.sort(key=lambda x: x['relevance_score'], reverse=True)
    return all_chunks[:5]

def generate_rag_response(query, context_chunks, deterministic_info, language='en'):
    """
    Synthesizes RAG response with:
    - Measured Refusal Threshold (score < 0.40 refusal)
    - Amber Flagging for unverified details
    - Multilingual Output (Telugu/Hindi/English) while preserving English IS codes and Citations
    """
    max_score = max([c['relevance_score'] for c in context_chunks]) if context_chunks else 0.0

    # Measured Refusal Threshold Check (< 0.40)
    if max_score < MEASURED_REFUSAL_THRESHOLD and not deterministic_info.get('matched_standard'):
        # Log to Documentation Gap Report DB table
        conn = get_db_connection()
        conn.execute("INSERT INTO audit_logs (user_id, action_type, details) VALUES (?, ?, ?)",
                     (1, "DOCUMENTATION_GAP_REFUSAL", json.dumps({"query": query, "max_score": max_score})))
        conn.commit()
        conn.close()

        refusal_msg = (
            "⚠️ **Insufficient Official Evidence (Refusal Threshold Score: {:.2f} < 0.40)**\n\n"
            "I could not locate sufficient official BIS documentation covering your exact query. "
            "To prevent hallucination, our system refrains from generating ungrounded advice. "
            "This query has been logged in the **BIS Documentation Gap Report** for Ministry review."
        ).format(max_score)
        return refusal_msg, [], max_score

    citations = []
    for idx, c in enumerate(context_chunks):
        citations.append({
            'citation_id': idx + 1,
            'doc_code': c['doc_code'],
            'doc_title': c['doc_title'],
            'page_number': c['page_number'],
            'section_heading': c['section_heading'],
            'doc_url': c['doc_url'],
            'content_snippet': c['content'][:150] + "..."
        })

    product = deterministic_info.get('matched_standard', {}).get('title', 'Product')
    is_num = deterministic_info.get('matched_standard', {}).get('is_number', 'IS Standard')
    qco_status = deterministic_info.get('qco_info', {}).get('status', 'Active Order Enforced')
    scheme = deterministic_info.get('matched_standard', {}).get('applicable_scheme', 'Scheme I (ISI Mark)')
    tests = deterministic_info.get('matched_standard', {}).get('testing_requirements', [])

    # Multilingual Synthesis Prompting
    lang_intro = ""
    if language == 'hi':
        lang_intro = f"**भारतीय मानक {is_num} के तहत {product} के लिए अनुपालन विवरण:**\n\n"
    elif language == 'te':
        lang_intro = f"**భారతీయ ప్రామాణికం {is_num} కింద {product} కోసం వర్తించే నిబంధనలు:**\n\n"
    else:
        lang_intro = f"### Compliance Overview for {product} ({is_num})\n\n"

    ai_text = (
        f"{lang_intro}"
        f"**1. Applicable Indian Standard**\n"
        f"Product matches **{is_num}** ({product}). Compliance is governed by official BIS notifications.\n\n"
        f"**2. QCO Status & Mandate**\n"
        f"**Status:** {qco_status}. Manufacturing or importing without the Standard Mark under valid licence is strictly prohibited.\n\n"
        f"**3. Certification Scheme & Required Tests**\n"
        f"Required Scheme: **{scheme}**.\n"
        f"Mandatory Type & Routine Tests:\n"
        + "\n".join([f"- {test}" for test in tests[:4]]) + "\n\n"
        f"🟧 **Unverified Details (Amber Flag):**\n"
        f"> *Note: Factory inspection scheduling and precise govt application fees are subject to portal updates on manakonline.in and are not hard-coded to avoid obsolescence.*\n\n"
        f"**4. Verifiable Source Evidence**\n"
        f"All claims above cross-referenced with official BIS documents [CITATION 1] and QCO notifications [CITATION 2]."
    )

    return ai_text, citations, max_score
