"""Durable confirmation emails, claimed atomically by background consumers."""
from datetime import datetime, timedelta
import hashlib
import json
import secrets
import threading

import requests
from sqlalchemy import and_, or_

from extensions import db
from models import EmailDelivery, Order


def _groups(order):
    from emailing import _group_tickets_by_recipient
    groups = _group_tickets_by_recipient(order)
    if not groups and order.email:
        groups = [(order.email, order.name, [])]
    return groups


def _revision(order, email, name, tickets):
    snapshot = [order.id, email, name, order.lang_code, order.total_cents,
                [[t.id, t.code, t.holder_name, t.holder_email] for t in sorted(tickets, key=lambda t: t.id)]]
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False).encode()).hexdigest()


def enqueue_order_emails(order, event_id="", recipients=None):
    db.session.flush()
    for email, name, tickets in _groups(order):
        if recipients is not None and email.lower() not in recipients:
            continue
        revision = _revision(order, email, name, tickets)
        key = hashlib.sha256((revision + event_id).encode()).hexdigest() if event_id else revision
        if db.session.get(EmailDelivery, key) is None:
            db.session.add(EmailDelivery(id=key, revision=revision, order_id=order.id, recipient=email))


def process_pending(app, limit=10):
    if not app.config.get("EMAILS_LIVE"):
        return
    with app.app_context():
        now = datetime.utcnow()
        eligible = or_(EmailDelivery.status == "pending",
                       and_(EmailDelivery.status == "sending", EmailDelivery.lease_until < now))
        ids = [row.id for row in EmailDelivery.query.filter(eligible, EmailDelivery.available_at <= now)
               .order_by(EmailDelivery.available_at).limit(limit).all()]
        db.session.rollback()
        for key in ids:
            now = datetime.utcnow()
            token = secrets.token_hex(16)
            claimed = EmailDelivery.query.filter(EmailDelivery.id == key, eligible,
                                                 EmailDelivery.available_at <= now).update({
                "status": "sending", "lease_until": now + timedelta(minutes=10), "lease_token": token,
                "attempts": EmailDelivery.attempts + 1}, synchronize_session=False)
            db.session.commit()
            if not claimed:
                continue
            job = db.session.get(EmailDelivery, key)
            try:
                order = db.session.get(Order, job.order_id)
                group = next((g for g in _groups(order) if _revision(order, *g) == job.revision), None) if order else None
                if not group or order.status not in {"paid", "comp"}:
                    job.status = "cancelled"
                    db.session.commit()
                    continue
                # Avoid automatic re-sending after the provider's 24 h deduplication window.
                if job.first_attempt_at and now - job.first_attempt_at > timedelta(hours=23):
                    job.status, job.last_error = "review", "Provider reconciliation required"
                    db.session.commit()
                    continue
                from emailing import _ticket_email_payload
                payload = _ticket_email_payload(order, app.config, *group)
                payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if job.payload_hash and job.payload_hash != payload_hash:
                    job.status, job.last_error = "review", "Payload changed; provider reconciliation required"
                    db.session.commit()
                    continue
                job.payload_hash = payload_hash
                if not job.first_attempt_at:
                    job.first_attempt_at = datetime.utcnow()
                    db.session.commit()
                response = requests.post("https://api.resend.com/emails", json=payload,
                                         headers={"Authorization": f"Bearer {app.config['RESEND_API_KEY']}",
                                                  "Idempotency-Key": key}, timeout=20)
                if response.status_code < 300:
                    job.status = "sent"
                    job.provider_message_id = str(response.json().get("id", ""))[:180]
                    db.session.flush()
                    order.email_sent = not EmailDelivery.query.filter(
                        EmailDelivery.order_id == order.id, EmailDelivery.status.in_(["pending", "sending", "failed", "review"])).count()
                elif response.status_code == 429 or response.status_code >= 500:
                    job.status = "pending"
                    job.available_at = datetime.utcnow() + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts, 7)))
                    job.last_error = f"HTTP {response.status_code}"
                else:
                    job.status, job.last_error = "failed", f"HTTP {response.status_code}"
                db.session.commit()
            except Exception:
                db.session.rollback()
                job = EmailDelivery.query.filter_by(id=key, lease_token=token).first()
                if job:
                    job.status = "pending"
                    job.last_error = "Email processing failed; retry scheduled"
                    job.available_at = datetime.utcnow() + timedelta(minutes=2)
                    db.session.commit()
                app.logger.warning("Email job %s failed; retained for retry", key[:12])


def init_outbox(app):
    if not app.config.get("EMAIL_WORKER_ENABLED"):
        return
    def consume():
        stop = threading.Event()
        while not stop.wait(5):
            try:
                process_pending(app)
                from website_jobs import process_website_jobs
                process_website_jobs(app)
            except Exception:
                app.logger.warning("Email queue temporarily unavailable")
    threading.Thread(target=consume, name="email-outbox", daemon=True).start()
