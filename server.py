"""Creates and configures the single Flask `app` instance: env/DB bootstrap,
the nav/language/active-case context processor, the onboarding gate, and the
'md' template filter. Route modules under `routes/` import `app` from here
and attach their view functions to it — this module never imports `routes`,
so there is no import-cycle between the two.
"""
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, session, url_for

from database import get_db_connection, init_db
from seed_data import seed_database
from services import llm
from services.mailer import smtp_is_configured
from translations import t as translate

from constants import NAV_LINKS, ONBOARDING_EXEMPT, SUPPORTED_LANGS
from helpers import render_markdown

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'bis_copilot_secret_key_sih2026_ianurag014')

init_db()
seed_database()

_llm_ok, _llm_detail = llm.check_connectivity()
print(f"[STARTUP] LLM: {'Gemini ON' if _llm_ok else 'offline'} - {_llm_detail}")
print(f"[STARTUP] Blend: {'70% BIS / 30% Gemini' if _llm_ok else '100% BIS (curated KB + ingested PDFs)'}")
print(f"[STARTUP] Translation: deep-translator (keyless) -> hi/te answer bodies")
print(f"[STARTUP] SMTP: {'configured' if smtp_is_configured() else 'dev mode (codes to console)'}")

app.add_template_filter(render_markdown, name='md')


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
    return {
        'nav_links': [(ep, translate('nav.' + ep, lang) or lbl)
                      for ep, lbl in NAV_LINKS],
        'supported_langs': SUPPORTED_LANGS,
        'current_lang': lang,
        'active_case': case,
        't': lambda key: translate(key, lang),
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
    if not session.get('active_case_id') and request.endpoint not in ONBOARDING_EXEMPT \
            and not request.path.startswith('/api/'):
        return redirect(url_for('onboarding'))
    return None
