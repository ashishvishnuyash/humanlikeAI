"""
Email utility — send transactional emails via Resend.

Requires env var: RESEND_API_KEY
Falls back to console log if the key is not set (dev mode).
"""

from __future__ import annotations

import os
import httpx

RESEND_API_URL = "https://api.resend.com/emails"
FROM_ADDRESS   = os.environ.get("EMAIL_FROM", "Diltak <noreply@diltak.com>")


def _send(to: str, subject: str, html: str) -> bool:
    """
    Send an email via Resend REST API.
    Returns True on success, False on failure.
    Never raises.
    """
    api_key = os.environ.get("RESEND_API_KEY")

    if not api_key:
        # Dev mode — just print to console
        print(f"[email] DEV MODE — would send to {to}")
        print(f"[email] Subject: {subject}")
        return True

    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": FROM_ADDRESS, "to": [to], "subject": subject, "html": html},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            return True
        print(f"[email] Resend error {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        print(f"[email] send error: {e}")
        return False


def send_invite_email(
    to_email: str,
    first_name: str,
    company_name: str,
    invite_link: str,
    sender_name: str = "",
) -> bool:
    """
    Send a new-employee invite email with a one-time password-set link.
    Returns True if sent successfully.
    """
    subject = f"You've been added to {company_name} on Diltak"
    by_line = f"{sender_name} has" if sender_name else "Your company has"

    html = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: auto; padding: 32px;">
      <h2 style="color: #1a1a2e;">Welcome to Diltak 👋</h2>
      <p>Hi {first_name},</p>
      <p>
        {by_line} created your account on <strong>Diltak</strong> —
        {company_name}'s mental wellness platform.
      </p>
      <p>Click the button below to set your password and get started:</p>
      <p style="margin: 32px 0;">
        <a href="{invite_link}"
           style="background:#6c63ff;color:#fff;padding:14px 28px;
                  border-radius:8px;text-decoration:none;font-weight:600;">
          Set My Password
        </a>
      </p>
      <p style="color:#888;font-size:13px;">
        This link expires in 72 hours. If you didn't expect this email, you can safely ignore it.
      </p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
      <p style="color:#aaa;font-size:12px;">Diltak · Mental Wellness for Teams</p>
    </div>
    """
    return _send(to_email, subject, html)


def send_welcome_email(
    to_email: str,
    first_name: str,
    company_name: str,
    temp_password: str,
) -> bool:
    """
    Send a welcome email with a temporary password.
    Used only when Firebase invite links are not available.
    """
    subject = f"Welcome to {company_name} on Diltak"
    html = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: auto; padding: 32px;">
      <h2 style="color: #1a1a2e;">Welcome to Diltak 👋</h2>
      <p>Hi {first_name},</p>
      <p>Your account has been created on <strong>Diltak</strong> — {company_name}'s mental wellness platform.</p>
      <p><strong>Email:</strong> {to_email}<br>
         <strong>Temporary password:</strong> <code>{temp_password}</code></p>
      <p style="color:#e74c3c;">Please log in and change your password immediately.</p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
      <p style="color:#aaa;font-size:12px;">Diltak · Mental Wellness for Teams</p>
    </div>
    """
    return _send(to_email, subject, html)
