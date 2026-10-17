"""Email the brief (HTML body + PDF attachment) via Gmail SMTP. A failure here never fails the run:
the brief is already saved in output/ (and uploaded as a GitHub Actions artifact)."""
from __future__ import annotations

import logging
import mimetypes
import os
import re
from email.message import EmailMessage
from email.utils import make_msgid
import smtplib
from pathlib import Path

from config import OUTPUT_DIR

log = logging.getLogger(__name__)


def _recipients() -> list[str]:
    return [r.strip() for r in (os.getenv("BRIEF_RECIPIENTS") or "").split(",") if r.strip()]


def _with_cids(html: str) -> tuple[str, list[tuple[str, bytes, str, str]]]:
    """Swap relative image paths (images/...) for cid: references. Returns (html, inline images)."""
    images = []
    for src in sorted(set(re.findall(r'src="(images/[^"]+)"', html))):
        path = OUTPUT_DIR / src
        if not path.exists():
            continue
        cid = make_msgid(domain="koshk.brief")
        html = html.replace(f'src="{src}"', f'src="cid:{cid[1:-1]}"')
        maintype, subtype = (mimetypes.guess_type(path.name)[0] or "image/jpeg").split("/")
        images.append((cid, path.read_bytes(), maintype, subtype))
    return html, images


def send_email(html_path: Path | None, pdf_path: Path | None, subject: str) -> dict:
    user, password, to = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"), _recipients()
    if not (user and password and to):
        reason = "SMTP_USER / SMTP_PASSWORD / BRIEF_RECIPIENTS not set"
        log.info(f"[email] SKIPPED: {reason}")
        return {"status": "skipped", "reason": reason}
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
        msg.set_content("Koshk Weekly Brief attached as PDF. Open in an HTML-capable client to see it inline.")
        if html_path and html_path.exists():
            html, images = _with_cids(html_path.read_text(encoding="utf-8"))
            msg.add_alternative(html, subtype="html")
            html_part = msg.get_payload()[1]
            for cid, data, maintype, subtype in images:
                html_part.add_related(data, maintype=maintype, subtype=subtype, cid=cid)
        if pdf_path and pdf_path.exists():
            msg.add_attachment(pdf_path.read_bytes(), maintype="application", subtype="pdf", filename=pdf_path.name)
        host, port = os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)
        log.info(f"[email] OK, sent to {len(to)} recipient(s)")
        return {"status": "sent", "recipients": len(to)}
    except Exception as e:  # never fail the run because of email
        reason = f"{type(e).__name__}: {str(e)[:200]}"
        log.warning(f"[email] FAILED: {reason}")
        return {"status": "failed", "reason": reason}
