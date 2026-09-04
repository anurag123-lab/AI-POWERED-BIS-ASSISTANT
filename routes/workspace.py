"""The language switch and the Personalised Home workspace."""
from flask import flash, redirect, render_template, request, session, url_for

from constants import SUPPORTED_LANGS
from database import get_db_connection
from helpers import active_case, get_full_user
from server import app
from services import answer_engine, knowledge_base as kb, llm


@app.route('/set-language', methods=['POST'])
def set_language():
    lang = (request.form.get('lang') or request.args.get('lang') or 'en').lower()
    if lang in SUPPORTED_LANGS:
        session['lang'] = lang
    return redirect(request.referrer or url_for('home'))


@app.route('/')
def index():
    if session.get('user_id'):
        return redirect(url_for('home'))
    return render_template('index.html')


@app.route('/home')
def home():
    """Personalised BIS workspace: 7 source-backed answers + AI assistant + history."""
    user, _onb = get_full_user(session['user_id'])
    if not user:
        session.clear()
        flash("Please sign in again.", "info")
        return redirect(url_for('login'))

    case = active_case()
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
