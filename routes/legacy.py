"""Old URL -> new endpoint 301 redirects, kept so bookmarked/shared links from
earlier milestones keep working."""
from flask import redirect, url_for

from server import app


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
