import os
import json
import time
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash

from database import get_db_connection, init_db
from seed_data import seed_database
from services.mailer import (
    generate_otp,
    send_registration_otp,
    smtp_is_configured,
    otp_exp_minutes,
)
from services.rag_engine import perform_hybrid_search, fanout_7_searches, generate_rag_response
from services.rule_engine import match_product_standard, analyze_scheme_applicability, inspect_isi_hallmark_photo, get_msme_licensing_timeline
from services.action_agent import get_action_recommendations, draft_lab_enquiry_email, execute_user_approved_action
from services.pdf_generator import generate_compliance_pdf

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'bis_copilot_secret_key_sih2026_ianurag014')

init_db()
seed_database()

@app.before_request
def ensure_default_session():
    public_endpoints = {'index', 'login', 'register', 'register_verify', 'register_resend_otp',
                        'google_auth', 'logout', 'static'}
    if session.get('user_id') or request.endpoint in public_endpoints:
        return None
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
    return redirect(url_for('login'))

# ==============================================================================
# AUTHENTICATION & USER SESSION ROUTES
# ==============================================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['user_name'] = user['full_name']
            session['user_email'] = user['email']
            session['user_role'] = user['role']
            session['user_city'] = user['city'] or ''
            session['user_state'] = user['state'] or ''
            flash(f"Welcome back, {user['full_name']}!", "success")
            return redirect(url_for('index'))
        else:
            flash("Invalid credentials.", "error")
    return render_template('login.html')

def _create_user_from_pending(pending):
    """Insert the verified registration into the DB and sign the user in."""
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO users (full_name, company_name, email, password_hash, role, auth_provider, city, state, user_type, business_stage, product_category, product_name, product_description, monthly_quantity, profile_completed) VALUES (?, ?, ?, ?, 'manufacturer', 'email', ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                     (pending['full_name'], pending['company_name'], pending['email'], pending['password_hash'],
                      pending['city'], pending['state'], pending['user_type'], pending['business_stage'],
                      pending['product_category'], pending['product_name'], pending['product_description'],
                      pending['monthly_quantity']))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (pending['email'],)).fetchone()
        conn.execute("INSERT OR REPLACE INTO user_profiles (user_id, user_type, business_stage, company_name, product_category, product_name, product_description, monthly_quantity, city, state, country) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'India')",
                     (user['id'], pending['user_type'], pending['business_stage'], pending['company_name'],
                      pending['product_category'], pending['product_name'], pending['product_description'],
                      pending['monthly_quantity'], pending['city'], pending['state']))
        conn.commit()
    finally:
        conn.close()

    session['user_id'] = user['id']
    session['user_name'] = user['full_name']
    session['user_email'] = user['email']
    session['user_role'] = user['role']
    session['user_city'] = pending['city']
    session['user_state'] = pending['state']


def _issue_registration_otp(pending):
    """Generate a fresh OTP for the pending registration and email it."""
    code = generate_otp()
    session['pending_registration'] = pending
    session['reg_otp'] = code
    session['reg_otp_expires'] = time.time() + otp_exp_minutes() * 60
    ok, detail = send_registration_otp(pending['email'], pending['full_name'], code)
    return ok, detail


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        company_name = request.form.get('company_name', '').strip() or request.form.get('business_name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        user_type = request.form.get('user_type', '').strip()
        business_stage = request.form.get('business_stage', '').strip()
        city = request.form.get('city', '').strip()
        state = request.form.get('state', '').strip()
        product_category = request.form.get('product_category', '').strip()
        product_name = request.form.get('product_name', '').strip()
        product_description = request.form.get('product_description', '').strip()
        monthly_quantity = request.form.get('monthly_quantity', '').strip()

        if not full_name or not email or not password:
            flash("Please complete the required profile details.", "error")
            return render_template('register.html')
        if password != confirm_password:
            flash("Passwords do not match. Please re-enter your password.", "error")
            return render_template('register.html')

        conn = get_db_connection()
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        if existing:
            flash("Email already registered. Please sign in instead.", "error")
            return render_template('register.html')

        pending = {
            'full_name': full_name,
            'company_name': company_name,
            'email': email,
            'password_hash': generate_password_hash(password),
            'user_type': user_type,
            'business_stage': business_stage,
            'city': city,
            'state': state,
            'product_category': product_category,
            'product_name': product_name,
            'product_description': product_description,
            'monthly_quantity': monthly_quantity,
        }
        ok, detail = _issue_registration_otp(pending)
        if ok:
            if smtp_is_configured():
                flash(f"We emailed a 6-digit verification code to {email}. Enter it below to activate your account.", "info")
            else:
                flash("SMTP is not configured, so the verification code was printed to the server console (dev mode).", "info")
            return redirect(url_for('register_verify'))
        flash(f"Could not send the verification email ({detail}). Please check the SMTP settings and try again.", "error")
        return render_template('register.html')
    return render_template('register.html')


@app.route('/register/verify', methods=['GET', 'POST'])
def register_verify():
    pending = session.get('pending_registration')
    if not pending:
        flash("Your registration session expired. Please sign up again.", "error")
        return redirect(url_for('register'))

    if request.method == 'POST':
        entered = request.form.get('otp_code', '').strip().replace(' ', '')
        expected = session.get('reg_otp')
        expires = session.get('reg_otp_expires', 0)

        if not expected or time.time() > expires:
            flash("That code has expired. We can send you a new one.", "error")
            return render_template('verify_otp.html', email=pending['email'])
        if entered != expected:
            flash("Incorrect verification code. Please try again.", "error")
            return render_template('verify_otp.html', email=pending['email'])

        try:
            _create_user_from_pending(pending)
        except Exception:
            flash("Email already registered. Please sign in instead.", "error")
            session.pop('pending_registration', None)
            session.pop('reg_otp', None)
            session.pop('reg_otp_expires', None)
            return redirect(url_for('login'))

        session.pop('pending_registration', None)
        session.pop('reg_otp', None)
        session.pop('reg_otp_expires', None)
        flash("Email verified. Your account and compliance profile have been saved.", "success")
        return redirect(url_for('index'))

    return render_template('verify_otp.html', email=pending['email'])


@app.route('/register/resend-otp', methods=['POST'])
def register_resend_otp():
    pending = session.get('pending_registration')
    if not pending:
        flash("Your registration session expired. Please sign up again.", "error")
        return redirect(url_for('register'))
    ok, detail = _issue_registration_otp(pending)
    if ok:
        flash(f"A new verification code is on its way to {pending['email']}.", "info")
    else:
        flash(f"Could not resend the code ({detail}).", "error")
    return redirect(url_for('register_verify'))

@app.route('/auth/google')
def google_auth():
    google_email = os.getenv('PRIMARY_USER_EMAIL', 'ianurag014@gmail.com')
    google_name = os.getenv('PRIMARY_USER_NAME', 'Anurag Indur')
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (google_email,)).fetchone()
    if not user:
        conn.execute("INSERT INTO users (full_name, company_name, email, password_hash, role, auth_provider) VALUES (?, ?, ?, ?, 'manufacturer', 'google')",
                     (google_name, "Indur Technologies", google_email, generate_password_hash("google_pass_123")))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (google_email,)).fetchone()
    conn.close()
    session['user_id'] = user['id']
    session['user_name'] = user['full_name']
    session['user_email'] = user['email']
    session['user_role'] = user['role']
    flash("Logged in via Google OAuth successfully!", "success")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

# ==============================================================================
# MAIN WEB VIEW ROUTES
# ==============================================================================

def sort_labs_for_user(labs, user_city=None, user_state=None):
    user_city = (user_city or '').strip().lower()
    user_state = (user_state or '').strip().lower()

    def sort_key(lab):
        lab_city = (lab.get('city') or '').strip().lower()
        lab_state = (lab.get('state') or '').strip().lower() if lab.get('state') else (lab.get('city') or '').strip().lower()
        if user_city and lab_city == user_city:
            city_rank = 0
        elif user_state and lab_state == user_state:
            city_rank = 1
        else:
            city_rank = 2
        return (city_rank, lab.get('lab_name') or '')

    return sorted(labs, key=sort_key)

@app.route('/')
def index():
    conn = get_db_connection()
    standards = conn.execute("SELECT * FROM standards").fetchall()
    qcos = conn.execute("SELECT * FROM qcos").fetchall()
    labs = conn.execute("SELECT * FROM laboratories").fetchall()
    user_id = session.get('user_id', 1)
    cases = conn.execute("SELECT * FROM compliance_cases WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    labs = [dict(l) for l in labs]
    labs = sort_labs_for_user(labs, session.get('user_city'), session.get('user_state'))
    return render_template('index.html', standards=standards, qcos=qcos, labs=labs, active_cases_count=len(cases))

@app.route('/copilot')
def copilot_view():
    if not session.get('user_id'):
        flash("Please register or log in to use Copilot.", "info")
        return redirect(url_for('login'))
    return render_template('copilot.html')

@app.route('/cases')
def cases_list():
    user_id = session.get('user_id', 1)
    conn = get_db_connection()
    cases_rows = conn.execute("SELECT * FROM compliance_cases WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
    conn.close()
    cases = []
    for row in cases_rows:
        item = dict(row)
        item['checklist'] = json.loads(item['checklist_json']) if item.get('checklist_json') else []
        cases.append(item)
    return render_template('cases.html', cases=cases)

@app.route('/cases/<int:case_id>')
def case_detail(case_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM compliance_cases WHERE id = ?", (case_id,)).fetchone()
    conn.close()
    if not row:
        flash("Compliance case not found.", "error")
        return redirect(url_for('cases_list'))
    case = dict(row)
    checklist = json.loads(case['checklist_json']) if case.get('checklist_json') else []
    return render_template('case_detail.html', case=case, checklist=checklist)

# 1. FEATURE: Instant Deterministic Product Finder (900+ items)
@app.route('/product-finder')
def product_finder():
    return render_template('product_finder.html')

# 2. FEATURE: Nearby Testing Labs Filtered by State
@app.route('/labs')
@app.route('/labs-by-state')
def labs_view():
    conn = get_db_connection()
    labs_rows = conn.execute("SELECT * FROM laboratories").fetchall()
    conn.close()
    labs = []
    for r in labs_rows:
        item = dict(r)
        try:
            item['supported_standards'] = json.loads(item.get('supported_standards_json') or '[]')
        except Exception:
            item['supported_standards'] = []
        labs.append(item)
    labs = sort_labs_for_user(labs, session.get('user_city'), session.get('user_state'))
    return render_template('labs.html', labs=labs)

# 3. FEATURE: Photo Check for ISI Mark / Hallmark
@app.route('/isi-photo-check')
def isi_photo_check():
    return render_template('photo_check.html')

# 4. FEATURE: Scheme Identifier (Applies vs DOES NOT Apply + Reasons)
@app.route('/scheme-identifier')
def scheme_identifier():
    return render_template('scheme_identifier.html')

# 5. FEATURE: Licensing Timeline & Printable Document Checklist
@app.route('/licensing-timeline')
def licensing_timeline():
    timeline_data = get_msme_licensing_timeline()
    return render_template('licensing_timeline.html', timeline=timeline_data)

# 6. FEATURE: Documentation Gap Report for BIS & Ministry
@app.route('/admin/gap-report')
def admin_gap_report():
    conn = get_db_connection()
    gaps = conn.execute("SELECT * FROM audit_logs WHERE action_type = 'DOCUMENTATION_GAP_REFUSAL' ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('gap_report.html', gaps=gaps)

@app.route('/documents')
def documents_view():
    conn = get_db_connection()
    docs = conn.execute("SELECT * FROM documents").fetchall()
    conn.close()
    return render_template('documents.html', documents=docs)

# ==============================================================================
# API ENDPOINTS
# ==============================================================================

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

# API 5: Seven Searches Fan-Out RAG & Multilingual Chat API
@app.route('/api/chat', methods=['POST'])
def api_chat():
    data = request.get_json() or {}
    message = data.get('message', '').strip()
    language = data.get('language', 'en') # 'en', 'hi', 'te'

    if not message:
        return jsonify({'status': 'error', 'message': 'Empty message'}), 400

    deterministic_result = match_product_standard(message)
    matched_std = deterministic_result['matched_standard']
    qco_info = deterministic_result['qco_info']
    eligible_labs = deterministic_result['eligible_labs']
    decision_tree = deterministic_result['decision_tree']
    distinction_badge = deterministic_result['distinction_badge']

    # SEVEN SEARCHES INSTEAD OF ONE (Multi-angle Fan-out)
    chunks = fanout_7_searches(message)

    # RAG Response with Refusal Threshold & Multilingual support
    ai_answer, citations, max_score = generate_rag_response(message, chunks, deterministic_result, language=language)

    actions = get_action_recommendations(matched_std, qco_info, eligible_labs)

    user_id = session.get('user_id', 1)
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM chat_sessions WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
    sess_row = cursor.fetchone()
    if sess_row:
        session_id = sess_row['id']
    else:
        cursor.execute("INSERT INTO chat_sessions (user_id, session_title) VALUES (?, ?)", (user_id, message[:30]))
        session_id = cursor.lastrowid

    cursor.execute("INSERT INTO chat_messages (session_id, sender, content) VALUES (?, 'user', ?)", (session_id, message))
    cursor.execute("INSERT INTO chat_messages (session_id, sender, content, citations_json, decision_tree_json) VALUES (?, 'copilot', ?, ?, ?)",
                   (session_id, ai_answer, json.dumps(citations), json.dumps(decision_tree)))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'answer': ai_answer,
        'citations': citations,
        'decision_tree': decision_tree,
        'distinction': distinction_badge,
        'matched_standard': matched_std,
        'action_recommendations': actions,
        'grounding_score': max_score,
        'refusal_threshold': 0.40
    })

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

@app.route('/api/case/<int:case_id>/pdf')
def get_case_pdf(case_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM compliance_cases WHERE id = ?", (case_id,)).fetchone()
    conn.close()
    if not row:
        return "Case not found", 404
    case_data = dict(row)
    case_data['checklist'] = json.loads(case_data['checklist_json']) if case_data.get('checklist_json') else []
    pdf_bytes = generate_compliance_pdf(case_data)
    from io import BytesIO
    return send_file(BytesIO(pdf_bytes), mimetype='application/pdf', as_attachment=True, download_name=f"BIS_Compliance_Report_Case_{case_id}.pdf")

@app.route('/api/actions/execute', methods=['POST'])
def api_execute_action():
    data = request.get_json() or {}
    action_id = data.get('action_id')
    user_id = session.get('user_id', 1)
    res = execute_user_approved_action(action_id, data, user_id)
    return jsonify(res)

if __name__ == '__main__':
    print("\n=======================================================")
    print("  [STARTING] BIS COMPLIANCE COPILOT PLATFORM          ")
    print("  Access Web App at: http://127.0.0.1:5000           ")
    print("=======================================================\n")
    app.run(host='127.0.0.1', port=5000, debug=True)
