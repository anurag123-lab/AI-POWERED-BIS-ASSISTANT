"""
SMTP mail helper for the BIS Compliance Copilot.

Sends transactional email (currently the registration OTP) through a standard
SMTP server. Configured entirely via environment variables so no credentials
live in the codebase:

    SMTP_HOST        default: smtp.gmail.com
    SMTP_PORT        default: 587  (STARTTLS)
    SMTP_USER        the sending Gmail address
    SMTP_PASSWORD    a Gmail *App Password* (not the account password)
    SMTP_FROM_NAME   friendly From name, default: "BIS Compliance Copilot"
    SMTP_FROM        override the From address (defaults to SMTP_USER)
    OTP_EXP_MINUTES  OTP validity window, default: 10

If SMTP_USER / SMTP_PASSWORD are not set the mailer runs in DEV mode: the
message (and the OTP) is printed to the server console instead of being sent,
so local development works without a mailbox.
"""

import os
import ssl
import random
import smtplib
from email.message import EmailMessage
from email.utils import formataddr


def _cfg():
    try:
        port = int(os.getenv("SMTP_PORT", "587") or "587")
    except ValueError:
        port = 587
    return {
        "host": (os.getenv("SMTP_HOST") or "smtp.gmail.com").strip(),
        "port": port,
        "user": os.getenv("SMTP_USER", "").strip(),
        # Gmail shows App Passwords as 4 space-separated groups; the real
        # secret has no spaces. Strip ALL whitespace so a pasted value works.
        "password": "".join(os.getenv("SMTP_PASSWORD", "").split()),
        "from_name": (os.getenv("SMTP_FROM_NAME") or "BIS Compliance Copilot").strip(),
        "from_addr": (os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "no-reply@bis-copilot.local").strip(),
    }


def smtp_is_configured():
    c = _cfg()
    return bool(c["user"] and c["password"])


def otp_exp_minutes():
    try:
        return max(1, int(os.getenv("OTP_EXP_MINUTES", "10")))
    except ValueError:
        return 10


def generate_otp(length=6):
    """Return a numeric one-time passcode as a string, e.g. '048213'."""
    return "".join(str(random.randint(0, 9)) for _ in range(length))


def send_email(to_addr, subject, text_body, html_body=None):
    """
    Send an email. Returns (ok: bool, detail: str).

    In DEV mode (no SMTP credentials) the email is logged to stdout and
    treated as sent so the caller's flow is not blocked.
    """
    c = _cfg()

    if not smtp_is_configured():
        print("\n" + "=" * 70)
        print("[MAILER:DEV MODE] SMTP not configured - email not actually sent")
        print(f"  To     : {to_addr}")
        print(f"  Subject: {subject}")
        print("  Body   :")
        for line in text_body.splitlines():
            print(f"    {line}")
        print("=" * 70 + "\n")
        return True, "dev-mode: logged to console"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((c["from_name"], c["from_addr"]))
    msg["To"] = to_addr
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        context = ssl.create_default_context()
        if c["port"] == 465:
            # Implicit TLS
            with smtplib.SMTP_SSL(c["host"], c["port"], timeout=20, context=context) as server:
                server.login(c["user"], c["password"])
                server.send_message(msg)
        else:
            # STARTTLS (587)
            with smtplib.SMTP(c["host"], c["port"], timeout=20) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(c["user"], c["password"])
                server.send_message(msg)
        print(f"[MAILER] Sent '{subject}' to {to_addr} via {c['host']}:{c['port']}")
        return True, "sent"
    except smtplib.SMTPAuthenticationError as exc:
        print(f"[MAILER:AUTH ERROR] {exc}")
        return False, ("SMTP login rejected — check SMTP_USER and that SMTP_PASSWORD "
                       "is a Gmail App Password (16 chars, 2-Step Verification enabled).")
    except Exception as exc:  # noqa: BLE001 - surface any SMTP failure to caller
        print(f"[MAILER:ERROR] Failed to send email to {to_addr}: {exc!r}")
        return False, str(exc)


def send_registration_otp(to_addr, full_name, code):
    """Send the registration verification code. Returns (ok, detail)."""
    mins = otp_exp_minutes()
    name = (full_name or "there").split(" ")[0]
    subject = f"{code} is your BIS Compliance Copilot verification code"
    text_body = (
        f"Hi {name},\n\n"
        f"Use the code below to finish creating your BIS Compliance Copilot account:\n\n"
        f"    {code}\n\n"
        f"This code expires in {mins} minutes. If you did not request this, you can ignore this email.\n\n"
        f"- BIS Compliance Copilot"
    )
    html_body = f"""\
<div style="font-family:Segoe UI,Arial,sans-serif;max-width:480px;margin:auto;color:#111">
  <h2 style="margin:0 0 12px">Verify your email</h2>
  <p style="margin:0 0 16px;color:#444">Hi {name}, use this code to finish creating your
     <strong>BIS Compliance Copilot</strong> account:</p>
  <div style="font-size:32px;font-weight:800;letter-spacing:8px;background:#f4f4f5;
              border:1px solid #e4e4e7;border-radius:10px;padding:18px;text-align:center">{code}</div>
  <p style="margin:16px 0 0;color:#666;font-size:13px">This code expires in {mins} minutes.
     If you didn't request it, you can safely ignore this email.</p>
</div>"""
    return send_email(to_addr, subject, text_body, html_body)
