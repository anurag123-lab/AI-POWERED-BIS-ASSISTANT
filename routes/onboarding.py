"""Conversational onboarding: user type -> product -> location -> workspace.
`?new=1` starts an ADDITIONAL product (My Cases -> Start Another Product)."""
import json

from flask import flash, redirect, render_template, request, session, url_for

from constants import INDIAN_STATES, ONB_QUESTIONS
from database import get_db_connection
from server import app
from services import knowledge_base as kb


@app.route('/onboarding', methods=['GET', 'POST'])
def onboarding():
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
