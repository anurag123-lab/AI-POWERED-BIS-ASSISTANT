"""Small shared helpers used by more than one route module: session/workspace
lookups, the search-history writer, the lab sort order and the Markdown-to-HTML
template filter. Kept free of the Flask `app` object so it has no import-order
dependency on `server.py` or `routes/`.
"""
import json
import re
import time

from flask import session
from markupsafe import Markup, escape
from werkzeug.security import generate_password_hash

from database import get_db_connection
from services.mailer import generate_otp, otp_exp_minutes, send_registration_otp


# ==============================================================================
# Session / workspace helpers
# ==============================================================================

def resume_workspace(user_id):
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


def create_user_from_pending(pending):
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


def issue_registration_otp(pending):
    """Generate a fresh OTP for the pending registration and email it."""
    code = generate_otp()
    session['pending_registration'] = pending
    session['reg_otp'] = code
    session['reg_otp_expires'] = time.time() + otp_exp_minutes() * 60
    ok, detail = send_registration_otp(pending['email'], pending['full_name'], code)
    return ok, detail


def active_case():
    """Return the current user's active workspace case row, or None."""
    cid = session.get('active_case_id')
    if not cid:
        return None
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM compliance_cases WHERE id = ?", (cid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def saved_areas(case):
    try:
        return json.loads(case.get('saved_areas_json') or '{}')
    except Exception:
        return {}


def save_search_history(query, result):
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


# ==============================================================================
# Markdown -> safe HTML (Jinja filter 'md', registered in server.py)
# ==============================================================================

def _inline_md(s):
    s = str(escape(s))
    s = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)',
               r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    s = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', s)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    return s


def render_markdown(text):
    """Lightweight, safe Markdown: bold, inline code, headings, bullet lists,
    [text](url) links and paragraphs. Enough for KB answer bodies."""
    if not text:
        return Markup("")
    out_blocks = []
    for block in re.split(r'\n{2,}', str(text).strip()):
        lines = block.split('\n')
        if all(l.lstrip().startswith(('- ', '* ')) for l in lines if l.strip()):
            items = "".join(f"<li>{_inline_md(l.lstrip()[2:])}</li>" for l in lines if l.strip())
            out_blocks.append(f"<ul>{items}</ul>")
        else:
            html = "<br>".join(_inline_md(l) for l in lines)
            out_blocks.append(f"<p>{html}</p>")
    return Markup("".join(out_blocks))
