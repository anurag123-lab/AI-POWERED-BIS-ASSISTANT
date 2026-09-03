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

# Ordered guided-tour walkthrough shown right after a user verifies their email.
TOUR_STEPS = [
    {'endpoint': 'product_finder',     'label': 'Find Your Standard',   'blurb': 'Search 900+ compulsory products and get the exact IS number that applies.'},
    {'endpoint': 'scheme_identifier',  'label': 'Check Your Scheme',    'blurb': 'See which BIS scheme covers your product — and which ones explicitly do not.'},
    {'endpoint': 'licensing_timeline', 'label': 'Licensing Timeline',   'blurb': 'A day-by-day roadmap and a printable document checklist for your licence.'},
    {'endpoint': 'labs_view',          'label': 'Labs Near You',        'blurb': 'Find NABL / BIS-recognised testing labs and draft an enquiry email.'},
    {'endpoint': 'isi_photo_check',    'label': 'Verify an ISI Mark',   'blurb': 'Check that an ISI mark / hallmark carries every mandatory element.'},
    {'endpoint': 'copilot_view',       'label': 'Ask the Copilot',      'blurb': 'A source-cited assistant for any BIS compliance question.'},
]


@app.context_processor
def inject_tour_context():
    """Expose tour state to every template (drives templates/partials/tour_bar.html)."""
    try:
        active = request.args.get('tour') == '1'
        idx = int(request.args.get('step', '1'))
    except (ValueError, TypeError):
        active, idx = False, 1

    ctx = {'active': False, 'index': idx, 'total': len(TOUR_STEPS)}
    if active and 1 <= idx <= len(TOUR_STEPS):
        this_step = TOUR_STEPS[idx - 1]
        if idx < len(TOUR_STEPS):
            nxt = TOUR_STEPS[idx]
            next_url = url_for(nxt['endpoint'], tour=1, step=idx + 1)
        else:
            next_url = url_for('home', tour='done')
        ctx.update({
            'active': True,
            'label': this_step['label'],
            'blurb': this_step['blurb'],
            'next_url': next_url,
            'skip_url': url_for('home', tour='done'),
            'percent': int(idx / len(TOUR_STEPS) * 100),
            'is_last': idx == len(TOUR_STEPS),
        })
    return {'tour_ctx': ctx, 'tour_steps': TOUR_STEPS}


@app.before_request
def ensure_default_session():
    public_endpoints = {'index', 'login', 'register', 'register_step2', 'register_step3',
                        'register_verify', 'register_resend_otp',
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
            return redirect(url_for('home'))
        else:
            flash("Invalid credentials. Check your email and password and try again.", "error")
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
        conn.execute("INSERT OR REPLACE INTO user_onboarding_profiles (user_id, persona_role, industry_sector, compliance_stage, product_name, product_description, monthly_production_quantity) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (user['id'], pending['user_type'] or 'Manufacturer', pending['product_category'] or 'Other',
                      pending['business_stage'] or 'Commercial production', pending['product_name'] or '',
                      pending['product_description'], pending['monthly_quantity']))
        conn.commit()
    finally:
        conn.close()

    session['user_id'] = user['id']
    session['user_name'] = user['full_name']
    session['user_email'] = user['email']
    session['user_role'] = user['role']
    session['user_city'] = pending['city']
    session['user_state'] = pending['state']
    session['show_tour'] = True
    session.pop('reg_wizard', None)


def get_full_user(user_id):
    """Load the persisted user row + onboarding profile for display on the hub."""
    conn = get_db_connection()
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        onb = conn.execute("SELECT * FROM user_onboarding_profiles WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return (dict(user) if user else None), (dict(onb) if onb else None)


def _wizard_guard(*required_keys):
    """Return a redirect to step 1 if the wizard session is missing earlier steps."""
    data = session.get('reg_wizard') or {}
    if not all(data.get(k) for k in required_keys):
        flash("Let's start your registration from the top.", "info")
        return redirect(url_for('register'))
    return None


def _issue_registration_otp(pending):
    """Generate a fresh OTP for the pending registration and email it."""
    code = generate_otp()
    session['pending_registration'] = pending
    session['reg_otp'] = code
    session['reg_otp_expires'] = time.time() + otp_exp_minutes() * 60
    ok, detail = send_registration_otp(pending['email'], pending['full_name'], code)
    return ok, detail


# ------------------------------------------------------------------
# Registration wizard — 3 server-driven steps, then the OTP screen.
# Answers accumulate in session['reg_wizard'] until email is verified.
# ------------------------------------------------------------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Step 1 of 3 — account details."""
    wiz = session.get('reg_wizard') or {}
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        company_name = request.form.get('company_name', '').strip() or request.form.get('business_name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        data = {'full_name': full_name, 'company_name': company_name, 'email': email}
        errors = {}
        if not full_name:
            errors['full_name'] = "Please enter your full name."
        if not email or '@' not in email:
            errors['email'] = "Enter a valid email address."
        if not password:
            errors['password'] = "Choose a password."
        elif len(password) < 6:
            errors['password'] = "Use at least 6 characters."
        if password and password != confirm_password:
            errors['confirm_password'] = "Passwords do not match."

        if not errors:
            conn = get_db_connection()
            existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            conn.close()
            if existing:
                errors['email'] = "That email is already registered. Sign in instead."

        if errors:
            return render_template('register.html', active_step=1, data=data, errors=errors)

        wiz.update({
            'full_name': full_name,
            'company_name': company_name,
            'email': email,
            'password_hash': generate_password_hash(password),
        })
        session['reg_wizard'] = wiz
        return redirect(url_for('register_step2'))

    return render_template('register.html', active_step=1, data=wiz, errors={})


@app.route('/register/step-2', methods=['GET', 'POST'])
def register_step2():
    """Step 2 of 3 — about you & your product."""
    guard = _wizard_guard('email', 'password_hash')
    if guard:
        return guard
    wiz = session['reg_wizard']

    if request.method == 'POST':
        fields = ['user_type', 'business_stage', 'product_category',
                  'product_name', 'product_description', 'monthly_quantity']
        data = {f: request.form.get(f, '').strip() for f in fields}
        errors = {}
        for f, msg in [('user_type', "Tell us who you are."),
                       ('business_stage', "Pick your business stage."),
                       ('product_category', "Pick a product category."),
                       ('product_name', "Name the product."),
                       ('monthly_quantity', "Choose a monthly quantity.")]:
            if not data[f]:
                errors[f] = msg
        if errors:
            return render_template('register_step2.html', active_step=2, data=data, errors=errors)

        wiz.update(data)
        session['reg_wizard'] = wiz
        return redirect(url_for('register_step3'))

    return render_template('register_step2.html', active_step=2, data=wiz, errors={})


@app.route('/register/step-3', methods=['GET', 'POST'])
def register_step3():
    """Step 3 of 3 — location & consent, then issue the OTP."""
    guard = _wizard_guard('email', 'password_hash', 'user_type')
    if guard:
        return guard
    wiz = session['reg_wizard']

    if request.method == 'POST':
        city = request.form.get('city', '').strip()
        state = request.form.get('state', '').strip()
        agree = request.form.get('agree_terms')
        data = {'city': city, 'state': state}
        errors = {}
        if not city:
            errors['city'] = "Enter your city."
        if not state:
            errors['state'] = "Enter your state."
        if not agree:
            errors['agree_terms'] = "Please accept the Terms & Conditions to continue."
        if errors:
            return render_template('register_step3.html', active_step=3, data=data, errors=errors)

        wiz.update({'city': city, 'state': state})
        session['reg_wizard'] = wiz

        pending = {
            'full_name': wiz['full_name'],
            'company_name': wiz.get('company_name', ''),
            'email': wiz['email'],
            'password_hash': wiz['password_hash'],
            'user_type': wiz.get('user_type', ''),
            'business_stage': wiz.get('business_stage', ''),
            'city': city,
            'state': state,
            'product_category': wiz.get('product_category', ''),
            'product_name': wiz.get('product_name', ''),
            'product_description': wiz.get('product_description', ''),
            'monthly_quantity': wiz.get('monthly_quantity', ''),
        }
        ok, detail = _issue_registration_otp(pending)
        if ok:
            if smtp_is_configured():
                flash(f"We emailed a 6-digit verification code to {pending['email']}. Enter it below to activate your account.", "info")
            else:
                flash("SMTP is not configured, so the verification code was printed to the server console (dev mode).", "info")
            return redirect(url_for('register_verify'))
        flash(f"Could not send the verification email ({detail}). Please check the SMTP settings and try again.", "error")
        return render_template('register_step3.html', active_step=3, data=data, errors={})

    return render_template('register_step3.html', active_step=3, data=wiz, errors={})


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
            return render_template('verify_otp.html', email=pending['email'], active_step=4)
        if entered != expected:
            flash("Incorrect verification code. Please try again.", "error")
            return render_template('verify_otp.html', email=pending['email'], active_step=4)

        try:
            _create_user_from_pending(pending)
        except Exception:
            flash("Email already registered. Please sign in instead.", "error")
            for k in ('pending_registration', 'reg_otp', 'reg_otp_expires', 'reg_wizard'):
                session.pop(k, None)
            return redirect(url_for('login'))

        for k in ('pending_registration', 'reg_otp', 'reg_otp_expires', 'reg_wizard'):
            session.pop(k, None)
        flash("Email verified. Your account and compliance profile have been saved.", "success")
        return redirect(url_for('home'))

    return render_template('verify_otp.html', email=pending['email'], active_step=4)


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
    session['user_city'] = (user['city'] if 'city' in user.keys() else '') or ''
    session['user_state'] = (user['state'] if 'state' in user.keys() else '') or ''
    flash("Logged in via Google OAuth successfully!", "success")
    return redirect(url_for('home'))

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
    if session.get('user_id'):
        return redirect(url_for('home'))
    return render_template('index.html')


@app.route('/home')
def home():
    """Post-signup hub: shows the saved profile + a card per feature + the guided tour."""
    user, onboarding = get_full_user(session['user_id'])
    if not user:
        session.clear()
        flash("Please sign in again.", "info")
        return redirect(url_for('login'))

    if request.args.get('tour') == 'done':
        session['show_tour'] = False
        flash("You're all set — jump into any tool below whenever you need it.", "success")
        return redirect(url_for('home'))

    conn = get_db_connection()
    case_count = conn.execute("SELECT COUNT(*) AS c FROM compliance_cases WHERE user_id = ?",
                              (user['id'],)).fetchone()['c']
    conn.close()

    profile_rows = [
        ("Full name", user.get('full_name')),
        ("Email", user.get('email')),
        ("Company", user.get('company_name')),
        ("You are a", user.get('user_type')),
        ("Business stage", user.get('business_stage')),
        ("Product category", user.get('product_category')),
        ("Product", user.get('product_name')),
        ("Monthly quantity", user.get('monthly_quantity')),
        ("Location", ", ".join([p for p in [user.get('city'), user.get('state')] if p])),
    ]
    profile_rows = [(k, v) for k, v in profile_rows if v]

    return render_template('hub.html', user=user, onboarding=onboarding,
                           profile_rows=profile_rows, case_count=case_count,
                           show_tour=session.get('show_tour', False))

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
