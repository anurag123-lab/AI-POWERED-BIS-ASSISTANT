"""JSON API endpoints: the AI orchestrator/chat, search history, case/PDF
actions, lab enquiries and a couple of legacy deterministic-analysis probes
kept for back-compat."""
import json
import time

from flask import jsonify, request, session

from constants import CHECKLIST_ROWS
from database import get_db_connection
from helpers import active_case, render_markdown, save_search_history
from server import app
from services import ai_orchestrator, answer_engine
from services.action_agent import execute_user_approved_action
from services.rule_engine import analyze_scheme_applicability, inspect_isi_hallmark_photo


# API 1: Product Finder Instant Search API (Deterministic, No AI, Instant)
@app.route('/api/products/search', methods=['POST'])
def api_product_search():
    data = request.get_json() or {}
    query = data.get('query', '').strip()
    conn = get_db_connection()
    if query:
        rows = conn.execute("SELECT * FROM compulsory_products WHERE product_name LIKE ? OR category LIKE ? OR is_number LIKE ?",
                            (f'%{query}%', f'%{query}%', f'%{query}%')).fetchall()
    else:
        rows = conn.execute("SELECT * FROM compulsory_products LIMIT 20").fetchall()
    conn.close()
    return jsonify({'status': 'success', 'count': len(rows), 'products': [dict(r) for r in rows]})


# API 2: State-wise Labs Filter API
@app.route('/api/labs/by-state', methods=['POST'])
def api_labs_by_state():
    data = request.get_json() or {}
    state = data.get('state', '').strip()
    conn = get_db_connection()
    if state and state.lower() != 'all':
        rows = conn.execute("SELECT * FROM laboratories WHERE city LIKE ? OR location LIKE ?", (f'%{state}%', f'%{state}%')).fetchall()
    else:
        rows = conn.execute("SELECT * FROM laboratories").fetchall()
    conn.close()
    return jsonify({'status': 'success', 'labs': [dict(r) for r in rows]})


# API 3: ISI Photo Inspection API
@app.route('/api/isi/photo-check', methods=['POST'])
def api_isi_photo_check():
    data = request.get_json() or {}
    text_or_filename = data.get('text', 'IS 302-2-25 CM/L-8765432')
    res = inspect_isi_hallmark_photo(text_or_filename)
    return jsonify({'status': 'success', 'analysis': res})


# API 4: Scheme Identifier API
@app.route('/api/schemes/analyze', methods=['POST'])
def api_schemes_analyze():
    data = request.get_json() or {}
    is_number = data.get('is_number', 'IS 302-2-25')
    res = analyze_scheme_applicability(is_number)
    return jsonify({'status': 'success', 'analysis': res})


# API 5: AI Assistant - 7-answer engine over the curated BIS knowledge base
@app.route('/api/ai', methods=['POST'])
def api_ai():
    """AI Orchestrator entry point: intent detection -> service router -> action.
    Body: {message, product_id?, service?, language?}. product_id defaults to the
    active workspace case; it is never assumed to be a single hard-coded product."""
    data = request.get_json() or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'status': 'error', 'message': 'Empty message'}), 400

    product_id = data.get('product_id') or session.get('active_case_id')
    language = data.get('language') or session.get('lang', 'en')
    current_service = data.get('service') or request.args.get('from')

    result = ai_orchestrator.orchestrate(product_id, message, current_service, language)
    return jsonify({'status': 'success', **result})


@app.route('/api/chat', methods=['POST'])
def api_chat():
    """Back-compat: Home's rich chat. Runs the orchestrator; for 'answer'/overview
    intents it returns the full answer_engine result so the 7 cards can refresh."""
    data = request.get_json() or {}
    message = data.get('message', '').strip()
    language = data.get('language') or session.get('lang', 'en')
    if not message:
        return jsonify({'status': 'error', 'message': 'Empty message'}), 400

    product_id = session.get('active_case_id')
    orch = ai_orchestrator.orchestrate(product_id, message, 'home', language)

    # A clear service-routing intent -> let the orchestrator navigate the user.
    if orch['action'] == 'navigate' or orch.get('intent') == 'product_info':
        return jsonify({'status': 'success', **orch})

    # Everything else: the Home assistant answers - from the BIS KB when it can
    # (mode seven / area), otherwise from general Gemini knowledge (mode general).
    case = active_case()
    slug = case.get('product_slug') if case else None
    location = {'city': case.get('city'), 'state': case.get('state')} if case else None
    result = answer_engine.answer_question(slug, message, location=location,
                                           language=language, allow_general=True)
    save_search_history(message, result)
    payload = {'status': 'success', 'intent': orch.get('intent'), 'action': 'answer'}
    payload.update(result)
    payload.setdefault('refusal_threshold', answer_engine.MEASURED_REFUSAL_THRESHOLD)
    return jsonify(payload)


@app.route('/api/history', methods=['GET', 'DELETE'])
def api_history():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
    conn = get_db_connection()
    if request.method == 'DELETE':
        conn.execute("DELETE FROM search_history WHERE user_id = ? AND case_id IS ?",
                     (uid, session.get('active_case_id')))
        conn.commit()
        conn.close()
        return jsonify({'status': 'success'})
    rows = conn.execute(
        "SELECT id, query, mode, area, created_at FROM search_history "
        "WHERE user_id = ? AND (case_id IS ? OR ? IS NULL) ORDER BY id DESC LIMIT 40",
        (uid, session.get('active_case_id'), session.get('active_case_id')),
    ).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'items': [dict(r) for r in rows]})


@app.route('/api/history/<int:hid>')
def api_history_item(hid):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM search_history WHERE id = ? AND user_id = ?", (hid, uid)).fetchone()
    conn.close()
    if not row:
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    item = dict(row)
    try:
        item['sources'] = json.loads(item.get('sources_json') or '[]')
    except Exception:
        item['sources'] = []
    return jsonify({'status': 'success', 'item': item})


@app.route('/api/case/create', methods=['POST'])
def api_create_case():
    data = request.get_json() or {}
    product_name = data.get('product_name', 'Manufactured Item')
    is_number = data.get('is_number', 'IS 302-2-25')

    conn = get_db_connection()
    cursor = conn.cursor()
    std_row = cursor.execute("SELECT * FROM standards WHERE is_number = ?", (is_number,)).fetchone()
    checklist = json.loads(std_row['testing_requirements_json']) if std_row else ["HV Breakdown Test", "Leakage Current Test", "In-House Calibration"]

    user_id = session.get('user_id', 1)
    cursor.execute('''
        INSERT INTO compliance_cases (user_id, product_name, is_number, qco_status, scheme, current_step, checklist_json)
        VALUES (?, ?, ?, 'Compulsory Order Enforced', 'Scheme I (ISI Mark)', 'Standard Identified', ?)
    ''', (user_id, product_name, is_number, json.dumps(checklist)))
    case_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return jsonify({'status': 'success', 'case_id': case_id})


@app.route('/api/actions/execute', methods=['POST'])
def api_execute_action():
    data = request.get_json() or {}
    action_id = data.get('action_id')
    user_id = session.get('user_id', 1)
    res = execute_user_approved_action(action_id, data, user_id)
    return jsonify(res)


@app.route('/api/ai/area', methods=['POST'])
def api_ai_area():
    """Progressive upgrade: return the 70/30 Gemini answer for one area so the
    page can swap it in after the instant deterministic render."""
    if not session.get('user_id'):
        return jsonify({'status': 'error'}), 401
    data = request.get_json() or {}
    area = (data.get('area') or '').strip()
    case = active_case()
    slug = (case or {}).get('product_slug') or data.get('slug')
    if not slug or not area:
        return jsonify({'status': 'error', 'message': 'slug/area required'}), 400
    lang = session.get('lang', 'en')
    v = answer_engine.answer_area(slug, area, question=(data.get('question') or None),
                                  language=lang, use_llm=True)
    return jsonify({
        'status': 'success', 'area': area,
        'body_html': render_markdown(v['body_md']),
        'blend': v['blend'], 'llm_used': v['llm_used'],
    })


@app.route('/api/case/save-area', methods=['POST'])
def api_case_save_area():
    """Mark a KB area as reviewed/completed on the active case (feeds the Checklist)."""
    if not session.get('user_id') or not session.get('active_case_id'):
        return jsonify({'status': 'error', 'message': 'No active workspace'}), 400
    data = request.get_json() or {}
    area = (data.get('area') or '').strip()
    status = (data.get('status') or 'Reviewed').strip()
    if status not in ('Not Started', 'In Progress', 'Reviewed', 'Completed'):
        status = 'Reviewed'
    conn = get_db_connection()
    row = conn.execute("SELECT saved_areas_json FROM compliance_cases WHERE id = ? AND user_id = ?",
                       (session['active_case_id'], session['user_id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Case not found'}), 404
    try:
        saved = json.loads(row['saved_areas_json'] or '{}')
    except Exception:
        saved = {}
    saved[area] = {'status': status, 'at': time.strftime('%Y-%m-%d %H:%M')}
    conn.execute("UPDATE compliance_cases SET saved_areas_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (json.dumps(saved), session['active_case_id']))
    conn.commit()
    conn.close()
    reviewed = sum(1 for v in saved.values() if (v or {}).get('status') in ('Reviewed', 'Completed'))
    return jsonify({'status': 'success', 'area': area, 'new_status': status,
                    'reviewed': reviewed, 'total': len(CHECKLIST_ROWS)})


@app.route('/api/labs/enquiry', methods=['POST'])
def api_labs_enquiry():
    """Send the (user-approved) testing enquiry email to the lab, cc the user."""
    if not session.get('user_id'):
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
    data = request.get_json() or {}
    lab_name = (data.get('lab_name') or '').strip()
    lab_email = (data.get('lab_email') or '').strip()
    subject = (data.get('subject') or f'BIS type-testing enquiry - {lab_name}').strip()
    body = (data.get('body') or '').strip()
    if not lab_email or '@' not in lab_email:
        return jsonify({'status': 'error', 'message': 'This lab has no contact email on record. '
                                                     'Use the "Open in Google Maps" link to find its listed contact.'}), 400

    from services.mailer import send_email
    user_email = session.get('user_email')
    to_list = lab_email + (f", {user_email}" if user_email else "")
    ok, detail = send_email(to_list, subject, body)

    if session.get('active_case_id'):
        try:
            conn = get_db_connection()
            conn.execute("UPDATE compliance_cases SET lab_enquiry_status = 'Sent' WHERE id = ?",
                         (session['active_case_id'],))
            conn.execute("INSERT INTO audit_logs (user_id, action_type, details) VALUES (?, ?, ?)",
                         (session['user_id'], 'LAB_ENQUIRY_SENT',
                          json.dumps({'lab': lab_name, 'email': lab_email, 'ok': ok, 'detail': detail})))
            conn.commit()
            conn.close()
        except Exception:
            pass

    if ok:
        return jsonify({'status': 'success', 'lab_name': lab_name, 'detail': detail,
                        'cc_user': bool(user_email)})
    return jsonify({'status': 'error', 'message': f'Could not send the enquiry ({detail}).'}), 502
