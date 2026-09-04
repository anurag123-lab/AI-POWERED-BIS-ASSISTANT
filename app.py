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
from services import ai_orchestrator

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'bis_copilot_secret_key_sih2026_ianurag014')

init_db()
seed_database()

_llm_ok, _llm_detail = llm.check_connectivity()
print(f"[STARTUP] LLM: {'ON (' + (llm.active_provider() or '?') + ')' if _llm_ok else 'offline'} - {_llm_detail}")
print(f"[STARTUP] Translation: deep-translator (keyless) -> hi/te answer bodies")
print(f"[STARTUP] SMTP: {'configured' if smtp_is_configured() else 'dev mode (codes to console)'}")

# Top navigation shown to a logged-in user with an active product workspace.
# (endpoint, label) — kept pointing at current endpoints; M3 renames the routes.
NAV_LINKS = [
    ('home',         'Home'),
    ('standards',    'Standards'),
    ('schemes',      'Schemes'),
    ('testing_labs', 'Testing & Labs'),
    ('licensing',    'Licensing'),
    ('documents',    'Documents'),
    ('checklist',    'Checklist'),
    ('my_cases',     'My Cases'),
    ('photo_check',  'Photo Check'),
]

SUPPORTED_LANGS = {'en': 'English', 'hi': 'हिंदी', 'te': 'తెలుగు'}

# Endpoints a logged-in user may hit before finishing onboarding.
_ONBOARDING_EXEMPT = {
    'onboarding', 'logout', 'static', 'set_language', 'index',
    'my_cases', 'case_detail', 'activate_case', 'get_case_pdf', 'google_auth',
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
    lang = session.get('lang', 'en')
    from translations import t as _t
    return {
        'nav_links': [(ep, _t('nav.' + ep, lang) if _t('nav.' + ep, lang) else lbl)
                      for ep, lbl in NAV_LINKS],
        'supported_langs': SUPPORTED_LANGS,
        'current_lang': lang,
        'active_case': case,
        't': lambda key: _t(key, lang),
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

def _resume_workspace(user_id):
    """On login, re-attach the user's most recent product workspace so they
    skip onboarding and land straight back on their Home. Returns the case id
    or None."""
    conn = get_db_connection()
    row = conn.execute(
        "SELECT id, product_slug, city, state FROM compliance_cases "
        "WHERE user_id = ? AND product_slug IS NOT NULL ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    if row:
        session['active_case_id'] = row['id']
        session['user_city'] = row['city'] or ''
        session['user_state'] = row['state'] or ''
        return row['id']
    session.pop('active_case_id', None)
    return None


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
            _resume_workspace(user['id'])
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
    _resume_workspace(user['id'])
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
    """Conversational onboarding: user type -> product -> location -> workspace.
    `?new=1` starts an ADDITIONAL product (My Cases -> Start Another Product)."""
    starting_new = request.args.get('new') == '1' or session.get('onb_new')
    if request.method == 'GET' and request.args.get('new') == '1':
        session['onb_new'] = True
        session.pop('onb', None)
        session.pop('active_case_id', None)
        return redirect(url_for('onboarding'))
    if session.get('active_case_id') and not starting_new:
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
            session.pop('onb_new', None)
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


# ==============================================================================
# FEATURE PAGES — each renders the ACTIVE product's BIS knowledge-base area.
# (spec sections 7-16). Old URLs are kept as 301 redirects.
# ==============================================================================

CHECKLIST_ROWS = [
    ("Applicable Standard",       "standards",    "standards"),
    ("Certification Requirement", "certification", "schemes"),
    ("BIS Scheme",               "scheme",       "schemes"),
    ("Testing Requirement",      "testing",      "testing_labs"),
    ("Recognised Laboratory",    "labs",         "testing_labs"),
    ("Required Documents",       "documents",    "documents"),
    ("Licensing Process",        "licensing",    "licensing"),
]


def _feature_case():
    """Return the active workspace case dict, or None (caller redirects)."""
    return _active_case()


def _saved_areas(case):
    try:
        return json.loads(case.get('saved_areas_json') or '{}')
    except Exception:
        return {}


def _area_page(template, css, areas, extra=None):
    """Shared render for a single-area feature page."""
    case = _feature_case()
    if not case:
        return redirect(url_for('onboarding'))
    slug = case.get('product_slug')
    meta = kb.product_meta(slug) or {}
    lang = session.get('lang', 'en')
    views = [answer_engine.answer_area(slug, a, language=lang) for a in areas]
    ctx = dict(case=case, product=meta, slug=slug, views=views, css=css,
               saved=_saved_areas(case))
    if extra:
        ctx.update(extra)
    return render_template(template, **ctx)


@app.route('/standards')
def standards():
    return _area_page('standards.html', 'standards.css', ['standards', 'related_standards'])


@app.route('/schemes')
def schemes():
    return _area_page('schemes.html', 'schemes.css', ['certification', 'scheme'])


LICENSING_PORTALS = [
    {"key": "manakonline", "name": "BIS Manak Online",
     "desc": "Apply for a Scheme I (ISI Mark) licence, track applications, pay fees.",
     "url": "https://www.manakonline.in/MANAK/login"},
    {"key": "crsbis", "name": "BIS CRS Portal",
     "desc": "Register a model under the Compulsory Registration Scheme (Scheme II).",
     "url": "https://www.crsbis.in/BIS/registration-page.do"},
    {"key": "bis_overview", "name": "BIS Product Certification",
     "desc": "Official process overview, Scheme I guidelines and fee schedule.",
     "url": "https://www.bis.gov.in/product-certification/product-certification-overview/?lang=en"},
    {"key": "lims", "name": "BIS Recognised Labs (LIMS)",
     "desc": "Find a BIS-recognised laboratory in your product's scope.",
     "url": "https://lims.bis.gov.in/home/labs/"},
    {"key": "care", "name": "BIS Care",
     "desc": "Verify a licence / registration number and file complaints.",
     "url": "https://www.bis.gov.in/"},
]


@app.route('/licensing')
def licensing():
    case = _feature_case()
    if not case:
        return redirect(url_for('onboarding'))
    slug = case.get('product_slug')
    meta = kb.product_meta(slug) or {}
    lang = session.get('lang', 'en')
    view = answer_engine.answer_area(slug, 'licensing', language=lang)

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

    return render_template('licensing.html', case=case, product=meta, slug=slug,
                           view=view, steps=steps, lic_sources=lic_sources,
                           portals=portals, saved=_saved_areas(case))


@app.route('/documents')
def documents():
    return _area_page('documents.html', 'documents.css', ['supporting'])


@app.route('/testing-labs')
def testing_labs():
    case = _feature_case()
    if not case:
        return redirect(url_for('onboarding'))
    slug = case.get('product_slug')
    meta = kb.product_meta(slug) or {}
    lang = session.get('lang', 'en')
    testing_view = answer_engine.answer_area(slug, 'testing', language=lang)

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
    return render_template('testing_labs.html', case=case, product=meta, slug=slug,
                           testing_view=testing_view, labs=labs, lab_states=states,
                           default_state=case.get('state'), saved=_saved_areas(case))


@app.route('/photo-check')
def photo_check():
    case = _feature_case()
    if not case:
        return redirect(url_for('onboarding'))
    return render_template('photo_check.html', case=case,
                           product=kb.product_meta(case.get('product_slug')) or {})


@app.route('/checklist')
def checklist():
    case = _feature_case()
    if not case:
        return redirect(url_for('onboarding'))
    saved = _saved_areas(case)
    items = [{"label": lbl, "area": ar, "endpoint": ep,
              "status": (saved.get(ar) or {}).get("status", "Not Started")}
             for lbl, ar, ep in CHECKLIST_ROWS]
    reviewed = sum(1 for it in items if it["status"] in ("Reviewed", "Completed"))
    return render_template('checklist.html', case=case,
                           product=kb.product_meta(case.get('product_slug')) or {},
                           items=items, reviewed=reviewed, total=len(items))


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
    views = [answer_engine.answer_area(slug, a, language=lang) for a in areas] if slug else []
    saved = _saved_areas(case)
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


@app.route('/admin/gap-report')
def admin_gap_report():
    conn = get_db_connection()
    gaps = conn.execute(
        "SELECT * FROM audit_logs WHERE action_type = 'DOCUMENTATION_GAP_REFUSAL' ORDER BY id DESC"
    ).fetchall()
    conn.close()
    # aggregate by extracted topic (details JSON)
    from collections import Counter
    topics = Counter()
    parsed = []
    for g in gaps:
        g = dict(g)
        try:
            d = json.loads(g.get('details') or '{}')
        except Exception:
            d = {}
        g['query'] = d.get('query', g.get('details', ''))
        g['product'] = d.get('product') or '-'
        g['topic'] = d.get('category') or 'unknown'
        topics[g['topic']] += 1
        parsed.append(g)
    top = topics.most_common(12)
    return render_template('gap_report.html', gaps=parsed, topics=top)


# ---- old URL -> new endpoint (301) ------------------------------------------
@app.route('/product-finder')
def _r_product_finder():
    return redirect(url_for('standards'), 301)

@app.route('/scheme-identifier')
def _r_scheme_identifier():
    return redirect(url_for('schemes'), 301)

@app.route('/labs')
@app.route('/labs-by-state')
def _r_labs():
    return redirect(url_for('testing_labs'), 301)

@app.route('/licensing-timeline')
def _r_licensing_timeline():
    return redirect(url_for('licensing'), 301)

@app.route('/isi-photo-check')
def _r_photo_check():
    return redirect(url_for('photo_check'), 301)

@app.route('/cases')
def _r_cases():
    return redirect(url_for('my_cases'), 301)

@app.route('/cases/<int:case_id>')
def _r_case_detail(case_id):
    return redirect(url_for('case_detail', case_id=case_id), 301)

@app.route('/copilot')
def _r_copilot():
    return redirect(url_for('home'), 301)

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

    # Navigation / product-info / unsupported -> return the orchestrator verdict.
    if orch['action'] != 'answer' or orch.get('intent') == 'product_info':
        return jsonify({'status': 'success', **orch})

    # Overview -> full KB result (mode seven/area) so Home can rebuild the cards.
    case = _active_case()
    slug = case.get('product_slug') if case else None
    location = {'city': case.get('city'), 'state': case.get('state')} if case else None
    result = answer_engine.answer_question(slug, message, location=location, language=language)
    _save_search_history(message, result)
    payload = {'status': 'success', 'intent': orch['intent'], 'action': 'answer'}
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
@app.route('/my-cases/<int:case_id>/pdf')
def get_case_pdf(case_id):
    from io import BytesIO
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
            v = answer_engine.answer_area(slug, area, language=lang)
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

@app.route('/api/actions/execute', methods=['POST'])
def api_execute_action():
    data = request.get_json() or {}
    action_id = data.get('action_id')
    user_id = session.get('user_id', 1)
    res = execute_user_approved_action(action_id, data, user_id)
    return jsonify(res)


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

if __name__ == '__main__':
    print("\n=======================================================")
    print("  [STARTING] BIS COMPLIANCE COPILOT PLATFORM          ")
    print("  Access Web App at: http://127.0.0.1:5000           ")
    print("=======================================================\n")
    app.run(host='127.0.0.1', port=5000, debug=True)
