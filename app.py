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
from services import llm
from services import knowledge_base as kb
from services import answer_engine

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'bis_copilot_secret_key_sih2026_ianurag014')

init_db()
seed_database()

_llm_ok, _llm_detail = llm.check_connectivity()
print(f"[STARTUP] OpenAI: {'connected' if _llm_ok else 'OFFLINE'} - {_llm_detail}")
print(f"[STARTUP] SMTP: {'configured' if smtp_is_configured() else 'dev mode (codes to console)'}")

# Top navigation shown to a logged-in user with an active product workspace.
# (endpoint, label) — kept pointing at current endpoints; M3 renames the routes.
NAV_LINKS = [
    ('home',               'Home'),
    ('product_finder',     'Standards'),
    ('scheme_identifier',  'Schemes'),
    ('labs_view',          'Testing & Labs'),
    ('licensing_timeline', 'Licensing'),
    ('documents_view',     'Documents'),
    ('checklist',          'Checklist'),
    ('cases_list',         'My Cases'),
    ('isi_photo_check',    'Photo Check'),
]

SUPPORTED_LANGS = {'en': 'English', 'hi': 'हिंदी', 'te': 'తెలుగు'}

# Endpoints a logged-in user may hit before finishing onboarding.
_ONBOARDING_EXEMPT = {
    'onboarding', 'logout', 'static', 'set_language', 'index',
    'my_cases', 'cases_list', 'google_auth',
}


@app.context_processor
def inject_globals():
    """Nav links, language state and the active product for every template."""
    case = None
    if session.get('user_id') and session.get('active_case_id'):
        try:
            conn = get_db_connection()
            row = conn.execute("SELECT * FROM compliance_cases WHERE id = ?",
                               (session['active_case_id'],)).fetchone()
            conn.close()
            case = dict(row) if row else None
        except Exception:
            case = None
    return {
        'nav_links': NAV_LINKS,
        'supported_langs': SUPPORTED_LANGS,
        'current_lang': session.get('lang', 'en'),
        'active_case': case,
    }


@app.before_request
def ensure_default_session():
    public_endpoints = {'index', 'login', 'register', 'register_verify',
                        'register_resend_otp', 'google_auth', 'logout', 'static'}
    if request.endpoint in public_endpoints:
        return None
    if not session.get('user_id'):
        if request.path.startswith('/api/'):
            return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
        return redirect(url_for('login'))
    # Logged in but hasn't picked a product yet -> conversational onboarding.
    if not session.get('active_case_id') and request.endpoint not in _ONBOARDING_EXEMPT \
            and not request.path.startswith('/api/'):
        return redirect(url_for('onboarding'))
    return None


import re as _re
from markupsafe import escape, Markup


@app.template_filter('md')
def render_markdown(text):
    """Lightweight, safe Markdown: bold, inline code, headings, bullet lists,
    [text](url) links and paragraphs. Enough for KB answer bodies."""
    if not text:
        return Markup("")
    out_blocks = []
    for block in _re.split(r'\n{2,}', str(text).strip()):
        lines = block.split('\n')
        if all(l.lstrip().startswith(('- ', '* ')) for l in lines if l.strip()):
            items = "".join(f"<li>{_inline_md(l.lstrip()[2:])}</li>" for l in lines if l.strip())
            out_blocks.append(f"<ul>{items}</ul>")
        else:
            html = "<br>".join(_inline_md(l) for l in lines)
            out_blocks.append(f"<p>{html}</p>")
    return Markup("".join(out_blocks))


def _inline_md(s):
    s = str(escape(s))
    s = _re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)',
                r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    s = _re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', s)
    s = _re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', s)
    s = _re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    return s


@app.route('/set-language', methods=['POST'])
def set_language():
    lang = (request.form.get('lang') or request.args.get('lang') or 'en').lower()
    if lang in SUPPORTED_LANGS:
        session['lang'] = lang
    return redirect(request.referrer or url_for('home'))

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
    """Insert the verified account (name + email + password only) and sign in.
    Product / user-type / location are collected afterwards in /onboarding."""
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO users (full_name, email, password_hash, role, auth_provider, profile_completed) "
            "VALUES (?, ?, ?, 'manufacturer', 'email', 0)",
            (pending['full_name'], pending['email'], pending['password_hash']),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (pending['email'],)).fetchone()
    finally:
        conn.close()

    session['user_id'] = user['id']
    session['user_name'] = user['full_name']
    session['user_email'] = user['email']
    session['user_role'] = user['role']
    session.pop('active_case_id', None)
    for k in ('pending_registration', 'reg_otp', 'reg_otp_expires', 'reg_wizard'):
        session.pop(k, None)


def get_full_user(user_id):
    """Load the persisted user row + onboarding profile."""
    conn = get_db_connection()
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        onb = conn.execute("SELECT * FROM user_onboarding_profiles WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return (dict(user) if user else None), (dict(onb) if onb else None)


def _issue_registration_otp(pending):
    """Generate a fresh OTP for the pending registration and email it."""
    code = generate_otp()
    session['pending_registration'] = pending
    session['reg_otp'] = code
    session['reg_otp_expires'] = time.time() + otp_exp_minutes() * 60
    ok, detail = send_registration_otp(pending['email'], pending['full_name'], code)
    return ok, detail


# ------------------------------------------------------------------
# Registration — name + email + password, then the OTP screen.
# Product / user type / location are asked afterwards in /onboarding.
# ------------------------------------------------------------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        data = {'full_name': full_name, 'email': email}
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
            return render_template('register.html', data=data, errors=errors)

        pending = {
            'full_name': full_name,
            'email': email,
            'password_hash': generate_password_hash(password),
        }
        ok, detail = _issue_registration_otp(pending)
        if ok:
            if smtp_is_configured():
                flash(f"We emailed a 6-digit verification code to {email}. Enter it below to activate your account.", "info")
            else:
                flash("SMTP is not configured, so the verification code was printed to the server console (dev mode).", "info")
            return redirect(url_for('register_verify'))
        flash(f"Could not send the verification email ({detail}). Please check the SMTP settings and try again.", "error")
        return render_template('register.html', data=data, errors={})

    return render_template('register.html', data={}, errors={})


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
            for k in ('pending_registration', 'reg_otp', 'reg_otp_expires', 'reg_wizard'):
                session.pop(k, None)
            return redirect(url_for('login'))

        flash("Email verified. Let's set up your BIS workspace.", "success")
        return redirect(url_for('onboarding'))

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


INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi (NCT)",
    "Jammu & Kashmir", "Ladakh", "Puducherry", "Chandigarh",
    "Andaman & Nicobar Islands", "Dadra & Nagar Haveli and Daman & Diu", "Lakshadweep",
]
USER_TYPES = ["Manufacturer", "Importer", "Trader / Distributor", "Startup / MSME",
              "Compliance consultant", "Student", "Consumer"]

ONB_QUESTIONS = [
    {"key": "user_type", "prompt": "To set up your workspace, what best describes you?",
     "type": "choice", "options": USER_TYPES},
    {"key": "product", "prompt": "Which product are you working with?",
     "type": "product"},
    {"key": "location", "prompt": "Where are you located?", "type": "location"},
]


@app.route('/onboarding', methods=['GET', 'POST'])
def onboarding():
    """Conversational onboarding: user type -> product -> location -> workspace."""
    if session.get('active_case_id'):
        return redirect(url_for('home'))
    onb = session.get('onb') or {}

    if request.method == 'POST':
        step = int(request.form.get('step', len(onb)))
        q = ONB_QUESTIONS[step] if 0 <= step < len(ONB_QUESTIONS) else None
        if q:
            if q["type"] == "choice":
                val = request.form.get(q["key"], "").strip()
                if val in q["options"]:
                    onb[q["key"]] = val
            elif q["type"] == "product":
                raw = request.form.get("product", "").strip()
                slug = kb.match_product(raw)
                if slug:
                    onb["product_slug"] = slug
                    onb["product_raw"] = raw
                else:
                    session['onb'] = onb
                    return render_template('onboarding.html', questions=ONB_QUESTIONS,
                                           onb=onb, step=step, states=INDIAN_STATES,
                                           kb_products=kb.list_products(),
                                           error=f"\"{raw}\" isn't in the BIS knowledge base yet. "
                                                 f"Supported now: {', '.join(kb.supported_names())}.")
            elif q["type"] == "location":
                onb["city"] = request.form.get("city", "").strip()
                onb["state"] = request.form.get("state", "").strip()
        session['onb'] = onb

        # all three collected? create the workspace case.
        if onb.get("user_type") and onb.get("product_slug") and onb.get("city") and onb.get("state"):
            meta = kb.product_meta(onb["product_slug"]) or {}
            prod = kb.get_product(onb["product_slug"]) or {}
            std = prod.get("areas", {}).get("standards", {}).get("primary", {})
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO compliance_cases (user_id, product_name, product_slug, category, is_number, "
                "qco_status, scheme, current_step, user_type, city, state, checklist_json, saved_areas_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'Onboarded', ?, ?, ?, ?, ?)",
                (session['user_id'], meta.get('display_name', onb.get('product_raw', '')),
                 onb["product_slug"], meta.get('category', ''), std.get('is_number', meta.get('is_number', '')),
                 'Under compulsory certification', meta.get('scheme', ''),
                 onb["user_type"], onb["city"], onb["state"], json.dumps([]), json.dumps({})),
            )
            case_id = cur.lastrowid
            cur.execute(
                "INSERT OR REPLACE INTO user_onboarding_profiles "
                "(user_id, persona_role, industry_sector, compliance_stage, product_name, product_description, monthly_production_quantity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session['user_id'], onb["user_type"], meta.get('category', 'Other'),
                 'Onboarded', meta.get('display_name', ''), '', ''),
            )
            cur.execute("UPDATE users SET user_type=?, city=?, state=?, product_name=?, profile_completed=1 WHERE id=?",
                        (onb["user_type"], onb["city"], onb["state"], meta.get('display_name', ''), session['user_id']))
            conn.commit()
            conn.close()
            session['active_case_id'] = case_id
            session['user_city'] = onb["city"]
            session['user_state'] = onb["state"]
            session.pop('onb', None)
            flash(f"Workspace ready for {meta.get('display_name', 'your product')}.", "success")
            return redirect(url_for('home'))

        return redirect(url_for('onboarding'))

    step = len(onb) if not onb.get("product_slug") or not onb.get("city") else 2
    step = min(step, len(ONB_QUESTIONS) - 1)
    # find first unanswered
    if not onb.get("user_type"):
        step = 0
    elif not onb.get("product_slug"):
        step = 1
    else:
        step = 2
    return render_template('onboarding.html', questions=ONB_QUESTIONS, onb=onb, step=step,
                           states=INDIAN_STATES, kb_products=kb.list_products(), error=None)


@app.route('/home')
def home():
    """Personalised BIS workspace: 7 source-backed answers + AI assistant + history."""
    user, _onb = get_full_user(session['user_id'])
    if not user:
        session.clear()
        flash("Please sign in again.", "info")
        return redirect(url_for('login'))

    case = _active_case()
    if not case:
        return redirect(url_for('onboarding'))

    slug = case.get('product_slug')
    meta = kb.product_meta(slug) or {}
    lang = session.get('lang', 'en')
    location = {'city': case.get('city'), 'state': case.get('state')}
    seven = answer_engine.answer_seven(slug, location, lang) if slug else []

    conn = get_db_connection()
    history = conn.execute(
        "SELECT id, query, mode, area, created_at FROM search_history "
        "WHERE user_id = ? AND case_id = ? ORDER BY id DESC LIMIT 40",
        (user['id'], case['id']),
    ).fetchall()
    conn.close()

    return render_template(
        'home.html',
        user=user, case=case, product=meta, slug=slug,
        seven=seven, history=[dict(h) for h in history],
        llm_active=llm.llm_available(),
    )


@app.route('/checklist')
def checklist():
    """Progress tracker across the 7 areas for the active product (full build in M3)."""
    case = _active_case()
    if not case:
        return redirect(url_for('onboarding'))
    try:
        saved = json.loads(case.get('saved_areas_json') or '{}')
    except Exception:
        saved = {}
    rows = [
        ("Applicable Standard", "standards"),
        ("Certification Requirement", "certification"),
        ("BIS Scheme", "scheme"),
        ("Testing Requirement", "testing"),
        ("Recognised Laboratory", "supporting"),
        ("Required Documents", "supporting"),
        ("Licensing Process", "licensing"),
    ]
    items = [{"label": lbl, "area": ar, "status": saved.get(ar, {}).get("status", "Not Started")}
             for lbl, ar in rows]
    reviewed = sum(1 for it in items if it["status"] in ("Reviewed", "Completed"))
    return render_template('checklist.html', case=case, items=items,
                           reviewed=reviewed, total=len(items))

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

def _active_case():
    """Return the current user's active workspace case row, or None."""
    cid = session.get('active_case_id')
    if not cid:
        return None
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM compliance_cases WHERE id = ?", (cid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _active_slug():
    case = _active_case()
    if case and case.get('product_slug'):
        return case['product_slug']
    return None


def _save_search_history(query, result):
    """Persist one AI-assistant turn for the Home sidebar."""
    mode = result.get('mode')
    answers = result.get('answers') or ([result['answer']] if result.get('answer') else [])
    # store a compact rendering of the answer(s)
    body = "\n\n---\n\n".join(a.get('body_md', '') for a in answers) if answers else ''
    sources = []
    for a in answers:
        sources.extend(a.get('sources', []) or [])
    area = answers[0].get('area') if answers and isinstance(answers[0], dict) else None
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO search_history (user_id, case_id, product_slug, query, mode, answer_md, sources_json, area, language) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session.get('user_id'), session.get('active_case_id'), result.get('product'),
             query, mode, body, json.dumps(sources), area, result.get('language', 'en')),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[api_chat] history save failed: {exc}")


# API 5: AI Assistant - 7-answer engine over the curated BIS knowledge base
@app.route('/api/chat', methods=['POST'])
def api_chat():
    data = request.get_json() or {}
    message = data.get('message', '').strip()
    language = data.get('language') or session.get('lang', 'en')

    if not message:
        return jsonify({'status': 'error', 'message': 'Empty message'}), 400

    case = _active_case()
    slug = case.get('product_slug') if case else None
    location = {'city': case.get('city'), 'state': case.get('state')} if case else None

    result = answer_engine.answer_question(slug, message, location=location, language=language)
    _save_search_history(message, result)

    payload = {'status': 'success'}
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
