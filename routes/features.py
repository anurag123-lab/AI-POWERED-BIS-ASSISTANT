"""Feature pages — each renders the ACTIVE product's BIS knowledge-base area
(spec sections 7-16). Old URLs are kept as 301 redirects in routes/legacy.py.
"""
import json

from flask import redirect, render_template, request, session, url_for

from constants import CHECKLIST_ROWS, LICENSING_PORTALS
from database import get_db_connection
from helpers import active_case, saved_areas, sort_labs_for_user
from server import app
from services import answer_engine, knowledge_base as kb


def _area_page(template, css, areas, extra=None):
    """Shared render for a single-area feature page."""
    case = active_case()
    if not case:
        return redirect(url_for('onboarding'))
    slug = case.get('product_slug')
    meta = kb.product_meta(slug) or {}
    lang = session.get('lang', 'en')
    # Render deterministic (100% BIS) so the page is instant; ai_upgrade.js then
    # swaps in the 70/30 Gemini answer per area via POST /api/ai/area.
    views = [answer_engine.answer_area(slug, a, language=lang, use_llm=False)
             for a in areas]
    ctx = dict(case=case, product=meta, slug=slug, views=views, css=css,
               saved=saved_areas(case), ai_upgrade=(areas[0] if areas else None))
    if extra:
        ctx.update(extra)
    return render_template(template, **ctx)


@app.route('/standards')
def standards():
    return _area_page('standards.html', 'standards.css', ['standards', 'related_standards'])


@app.route('/schemes')
def schemes():
    return _area_page('schemes.html', 'schemes.css', ['scheme', 'certification'])


@app.route('/licensing')
def licensing():
    case = active_case()
    if not case:
        return redirect(url_for('onboarding'))
    slug = case.get('product_slug')
    meta = kb.product_meta(slug) or {}
    lang = session.get('lang', 'en')
    view = answer_engine.answer_area(slug, 'licensing', language=lang, use_llm=False)

    prod = kb.get_product(slug) or {}
    lic = prod.get('areas', {}).get('licensing', {}) or {}
    steps = lic.get('steps', [])
    lic_sources = lic.get('sources', [])

    # Which portals are relevant: CRS for Scheme II products, Manak Online otherwise;
    # always include the BIS overview + LIMS + Care.
    scheme = (meta.get('scheme') or '').lower()
    is_crs = 'scheme ii' in scheme or 'crs' in scheme or 'registration' in scheme
    portals = [p for p in LICENSING_PORTALS
               if p['key'] not in (('manakonline',) if is_crs else ('crsbis',))]

    return render_template('licensing.html', ai_upgrade='licensing', case=case, product=meta, slug=slug,
                           view=view, steps=steps, lic_sources=lic_sources,
                           portals=portals, saved=saved_areas(case))


@app.route('/documents')
def documents():
    return _area_page('documents.html', 'documents.css', ['supporting'])


@app.route('/testing-labs')
def testing_labs():
    case = active_case()
    if not case:
        return redirect(url_for('onboarding'))
    slug = case.get('product_slug')
    meta = kb.product_meta(slug) or {}
    lang = session.get('lang', 'en')
    testing_view = answer_engine.answer_area(slug, 'testing', language=lang, use_llm=False)

    prod = kb.get_product(slug) or {}
    kb_labs = (prod.get('areas', {}).get('labs', {}) or {})
    labs = []
    for e in kb_labs.get('entries', []):
        labs.append({
            'name': e.get('name'), 'city': e.get('city'), 'state': e.get('state'),
            'scope': e.get('scope'), 'email': '', 'source': 'BIS knowledge base',
        })
    # plus any rows from the laboratories table whose standards match
    try:
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM laboratories").fetchall()
        conn.close()
        isnum = (meta.get('is_number') or '').split(':')[0].strip()
        for r in rows:
            r = dict(r)
            try:
                stds = json.loads(r.get('supported_standards_json') or '[]')
            except Exception:
                stds = []
            if not isnum or any(isnum.split()[0] in str(s) for s in stds):
                labs.append({
                    'name': r.get('lab_name'), 'city': r.get('city'),
                    'state': r.get('state') or r.get('city'), 'scope': ", ".join(stds),
                    'email': r.get('contact_email') or '', 'source': 'BIS lab directory',
                })
    except Exception:
        pass

    labs = sort_labs_for_user(labs, case.get('city'), case.get('state'))
    states = sorted({(l.get('state') or '').strip() for l in labs if l.get('state')})
    return render_template('testing_labs.html', ai_upgrade='testing', case=case, product=meta, slug=slug,
                           testing_view=testing_view, labs=labs, lab_states=states,
                           default_state=case.get('state'), saved=saved_areas(case))


@app.route('/photo-check')
def photo_check():
    case = active_case()
    if not case:
        return redirect(url_for('onboarding'))
    return render_template('photo_check.html', case=case,
                           product=kb.product_meta(case.get('product_slug')) or {})


@app.route('/checklist')
def checklist():
    case = active_case()
    if not case:
        return redirect(url_for('onboarding'))
    saved = saved_areas(case)
    items = [{"label": lbl, "area": ar, "endpoint": ep,
              "status": (saved.get(ar) or {}).get("status", "Not Started")}
             for lbl, ar, ep in CHECKLIST_ROWS]
    reviewed = sum(1 for it in items if it["status"] in ("Reviewed", "Completed"))
    return render_template('checklist.html', case=case,
                           product=kb.product_meta(case.get('product_slug')) or {},
                           items=items, reviewed=reviewed, total=len(items))
