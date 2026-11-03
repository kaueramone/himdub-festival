"""Durable small website tasks; never send while handling a public form."""

from datetime import datetime, timedelta
import hashlib
import json
from urllib.parse import quote
import requests
from sqlalchemy import and_, or_
from extensions import db
from models import WebsiteJob, NewsletterSubscriber


def enqueue(kind, payload, key):
    identifier = hashlib.sha256((kind + ":" + key).encode()).hexdigest()
    if not db.session.get(WebsiteJob, identifier):
        db.session.add(
            WebsiteJob(
                id=identifier,
                kind=kind,
                payload=json.dumps(payload, ensure_ascii=False),
            )
        )


def process_website_jobs(app, limit=5):
    if not app.config.get("EMAILS_LIVE"):
        return
    with app.app_context():
        now = datetime.utcnow()
        eligible = or_(
            WebsiteJob.status == "pending",
            and_(WebsiteJob.status == "sending", WebsiteJob.lease_until < now),
        )
        ids = [
            j.id
            for j in WebsiteJob.query.filter(eligible, WebsiteJob.available_at <= now)
            .order_by(WebsiteJob.created_at)
            .limit(limit)
        ]
        db.session.rollback()
        for identifier in ids:
            now = datetime.utcnow()
            claimed = WebsiteJob.query.filter(
                WebsiteJob.id == identifier, eligible, WebsiteJob.available_at <= now
            ).update(
                {
                    "status": "sending",
                    "lease_until": now + timedelta(minutes=5),
                    "attempts": WebsiteJob.attempts + 1,
                },
                synchronize_session=False,
            )
            db.session.commit()
            if not claimed:
                continue
            job = db.session.get(WebsiteJob, identifier)
            try:
                if job.first_attempt_at and now - job.first_attempt_at > timedelta(
                    hours=23
                ):
                    job.status = "review"
                    job.last_error = "Reconciliação com fornecedor necessária"
                    db.session.commit()
                    continue
                if not job.first_attempt_at:
                    job.first_attempt_at = now
                    db.session.commit()
                payload = json.loads(job.payload)
                headers = {
                    "Authorization": "Bearer " + app.config["RESEND_API_KEY"],
                    "Idempotency-Key": identifier,
                }
                if job.kind == "transfer_refund":
                    import stripe
                    from models import TicketTransfer

                    transfer = db.session.get(TicketTransfer, payload["transfer_id"])
                    if (
                        not transfer
                        or transfer.status != "refund_pending"
                        or not transfer.payment_intent
                    ):
                        job.status = "review"
                        job.last_error = "Reembolso requer verificação"
                        db.session.commit()
                        continue
                    stripe.api_key = app.config["STRIPE_SECRET_KEY"]
                    stripe.Refund.create(
                        payment_intent=transfer.payment_intent,
                        amount=transfer.fee_cents,
                        idempotency_key="transfer-refund-" + transfer.id,
                    )
                    transfer.status = "refunded"
                    job.status = "sent"
                    job.last_error = ""
                    db.session.commit()
                    continue
                if job.kind == "email":
                    response = requests.post(
                        "https://api.resend.com/emails",
                        headers=headers,
                        json=payload,
                        timeout=20,
                    )
                elif job.kind == "newsletter_sync":
                    subscriber = (
                        NewsletterSubscriber.query.filter_by(
                            id=payload["subscriber_id"]
                        )
                        .populate_existing()
                        .with_for_update()
                        .first()
                    )
                    if not subscriber or not subscriber.confirmed_at:
                        job.status = "cancelled"
                        db.session.commit()
                        continue
                    segment = app.config.get("RESEND_NEWSLETTER_SEGMENT_ID", "")
                    if not segment:
                        job.status = "failed"
                        job.last_error = "Segmento newsletter por configurar"
                        db.session.commit()
                        continue
                    headers["Authorization"] = "Bearer " + app.config.get(
                        "RESEND_MARKETING_API_KEY", app.config["RESEND_API_KEY"]
                    )
                    contact_url = "https://api.resend.com/contacts/" + quote(
                        subscriber.email, safe=""
                    )
                    response = requests.get(contact_url, headers=headers, timeout=20)
                    if response.status_code == 404:
                        headers["Idempotency-Key"] = identifier + "-create"
                        response = requests.post(
                            "https://api.resend.com/contacts",
                            headers=headers,
                            json={
                                "email": subscriber.email,
                                "unsubscribed": bool(subscriber.unsubscribed_at),
                            },
                            timeout=20,
                        )
                        if response.status_code < 300:
                            subscriber.provider_contact_id = str(
                                response.json().get("id", "")
                            )[:100]
                    elif response.status_code == 200:
                        existing = response.json()
                        subscriber.provider_contact_id = str(existing.get("id", ""))[
                            :100
                        ]
                        if existing.get("unsubscribed"):
                            # Never silently override an opt-out made in Resend.
                            subscriber.unsubscribed_at = (
                                subscriber.unsubscribed_at or datetime.utcnow()
                            )
                        elif subscriber.unsubscribed_at:
                            headers["Idempotency-Key"] = identifier + "-unsubscribe"
                            response = requests.patch(
                                contact_url,
                                headers=headers,
                                json={"unsubscribed": True},
                                timeout=20,
                            )
                    if response.status_code < 300 and not subscriber.unsubscribed_at:
                        headers["Idempotency-Key"] = identifier + "-segment"
                        response = requests.post(
                            contact_url + "/segments/" + segment,
                            headers=headers,
                            timeout=20,
                        )
                    if response.status_code < 300:
                        subscriber.synced_at = datetime.utcnow()
                else:
                    job.status = "failed"
                    job.last_error = "Tipo de tarefa desconhecido"
                    db.session.commit()
                    continue
                if response.status_code < 300:
                    job.status = "sent"
                    job.last_error = ""
                    # Remove private confirmation URLs after acceptance by the provider.
                    if job.kind == "email":
                        job.payload = "{}"
                elif response.status_code == 429 or response.status_code >= 500:
                    job.status = "pending"
                    job.available_at = now + timedelta(
                        seconds=min(3600, 30 * 2 ** min(job.attempts, 7))
                    )
                    job.last_error = "HTTP " + str(response.status_code)
                else:
                    job.status = "failed"
                    job.last_error = "HTTP " + str(response.status_code)
                db.session.commit()
            except Exception:
                db.session.rollback()
                job = db.session.get(WebsiteJob, identifier)
                if job:
                    job.status = "pending"
                    job.last_error = "Tarefa adiada; nova tentativa agendada"
                    job.available_at = datetime.utcnow() + timedelta(minutes=2)
                    db.session.commit()
                app.logger.warning("Website task failed; retained for retry")
