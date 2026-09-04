"""Documentation Gap Report — aggregates the measured-refusal audit log."""
import json
from collections import Counter

from flask import render_template

from database import get_db_connection
from server import app


@app.route('/admin/gap-report')
def admin_gap_report():
    conn = get_db_connection()
    gaps = conn.execute(
        "SELECT * FROM audit_logs WHERE action_type = 'DOCUMENTATION_GAP_REFUSAL' ORDER BY id DESC"
    ).fetchall()
    conn.close()
    # aggregate by extracted topic (details JSON)
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
