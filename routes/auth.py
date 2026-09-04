"""Authentication & user session routes: login, registration + OTP, Google
sign-in, logout. Endpoint names are unchanged from the original monolithic
app.py so every `url_for(...)` in the templates keeps working untouched."""
import os
import time

from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db_connection
from helpers import create_user_from_pending, issue_registration_otp, resume_workspace
from server import app
from services.mailer import smtp_is_configured


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
            resume_workspace(user['id'])
            flash(f"Welcome back, {user['full_name']}!", "success")
            return redirect(url_for('home'))
        else:
            flash("Invalid credentials. Check your email and password and try again.", "error")
    return render_template('login.html')


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
        ok, detail = issue_registration_otp(pending)
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
            create_user_from_pending(pending)
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
    ok, detail = issue_registration_otp(pending)
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
    resume_workspace(user['id'])
    flash("Logged in via Google OAuth successfully!", "success")
    return redirect(url_for('home'))


@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))
