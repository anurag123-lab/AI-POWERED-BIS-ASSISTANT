"""My Cases: the list of a user's product workspaces, one case's aggregated
view, switching the active case, and the multilingual PDF export."""
import json
from io import BytesIO

from flask import flash, redirect, render_template, request, send_file, session, url_for

from constants import CHECKLIST_ROWS
from database import get_db_connection
from helpers import saved_areas
from server import app
from services import answer_engine, knowledge_base as kb
from services.pdf_generator import generate_compliance_pdf


@app.route('/my-cases')
def my_cases():
    uid = session.get('user_id')
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM compliance_cases WHERE user_id = ? ORDER BY id DESC", (uid,)
    ).fetchall()
    conn.close()
    cases = []
    for row in rows:
        it = dict(row)
        saved = {}
        try:
            saved = json.loads(it.get('saved_areas_json') or '{}')
        except Exception:
            pass
        it['reviewed'] = sum(1 for a in saved.values()
                             if (a or {}).get('status') in ('Reviewed', 'Completed'))
        it['total'] = len(CHECKLIST_ROWS)
        it['is_active'] = (it['id'] == session.get('active_case_id'))
        cases.append(it)
    return render_template('my_cases.html', cases=cases)


@app.route('/my-cases/<int:case_id>')
def case_detail(case_id):
    uid = session.get('user_id')
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM compliance_cases WHERE id = ? AND user_id = ?",
                       (case_id, uid)).fetchone()
    conn.close()
    if not row:
        flash("Case not found.", "error")
        return redirect(url_for('my_cases'))
    case = dict(row)
    slug = case.get('product_slug')
    meta = kb.product_meta(slug) or {}
    lang = session.get('lang', 'en')
    areas = ['standards', 'related_standards', 'certification', 'scheme',
             'testing', 'licensing', 'supporting']
    views = [answer_engine.answer_area(slug, a, language=lang, use_llm=False) for a in areas] if slug else []
    saved = saved_areas(case)
    return render_template('case_detail.html', case=case, product=meta, slug=slug,
                           views=views, saved=saved)


@app.route('/my-cases/<int:case_id>/activate', methods=['POST'])
def activate_case(case_id):
    uid = session.get('user_id')
    conn = get_db_connection()
    row = conn.execute("SELECT id, city, state FROM compliance_cases WHERE id = ? AND user_id = ?",
                       (case_id, uid)).fetchone()
    conn.close()
    if row:
        session['active_case_id'] = row['id']
        session['user_city'] = row['city'] or ''
        session['user_state'] = row['state'] or ''
        flash("Switched workspace.", "success")
    return redirect(url_for('home'))


@app.route('/api/case/<int:case_id>/pdf')
@app.route('/my-cases/<int:case_id>/pdf')
def get_case_pdf(case_id):
    from services.pdf_generator import generate_case_pdf
    from translations import t as _t

    uid = session.get('user_id')
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM compliance_cases WHERE id = ? AND (user_id = ? OR ? IS NULL)",
                       (case_id, uid, uid)).fetchone()
    conn.close()
    if not row:
        return "Case not found", 404
    case = dict(row)

    lang = (request.args.get('lang') or session.get('lang', 'en')).lower()
    if lang not in ('en', 'hi', 'te'):
        lang = 'en'
    slug = case.get('product_slug')
    views = []
    if slug:
        for area in ['standards', 'related_standards', 'certification', 'scheme',
                     'testing', 'licensing', 'supporting']:
            v = answer_engine.answer_area(slug, area, language=lang, use_llm=False)
            if not v.get('refused'):
                views.append(v)

    if views:
        pdf_bytes = generate_case_pdf(case, views, language=lang,
                                     report_title=_t('pdf.title', lang))
    else:
        case['checklist'] = json.loads(case['checklist_json']) if case.get('checklist_json') else []
        pdf_bytes = generate_compliance_pdf(case)

    name = f"BIS_Report_{(slug or 'case')}_{lang}.pdf"
    return send_file(BytesIO(pdf_bytes), mimetype='application/pdf',
                     as_attachment=True, download_name=name)
