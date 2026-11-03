"""Explicit newsletter consent, confirmation and opt-out; broadcasts live in Resend."""

from datetime import datetime, timedelta
import csv
import io
import re
import secrets
from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.exc import IntegrityError
from auth import check_login_throttle
from extensions import db
from models import NewsletterSubscriber, WebsiteJob
from website_jobs import enqueue

CONSENT = "Quero receber novidades, programação e informações de venda do HIM Dub Festival por email. Posso cancelar a qualquer momento."


def serializer():
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"], salt="newsletter-confirm-v1"
    )


def subscribe():
    email = request.form.get("email", "").strip().lower()
    if len(email) > 180 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        abort(400, description="Introduz um email válido.")
    if request.form.get("consent") != "yes":
        abort(400, description="Confirma que queres receber a newsletter.")
    check_login_throttle("newsletter:" + email)
    subscriber = (
        NewsletterSubscriber.query.filter_by(email=email).with_for_update().first()
    )
    now = datetime.utcnow()
    # Same response for registered/unknown addresses; suppress repeated confirmation mail.
    if not subscriber or (
        not (subscriber.confirmed_at and not subscriber.unsubscribed_at)
        and now - subscriber.requested_at >= timedelta(hours=1)
    ):
        subscriber = subscriber or NewsletterSubscriber(
            email=email, nonce=secrets.token_hex(24), consent_text=CONSENT
        )
        subscriber.nonce = secrets.token_hex(24)
        subscriber.requested_at = now
        db.session.add(subscriber)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            flash(
                "Se necessário, receberás um email para confirmar a subscrição.",
                "success",
            )
            return redirect(url_for("main.home"))
        token = serializer().dumps(
            {"id": subscriber.id, "nonce": subscriber.nonce, "action": "confirm"}
        )
        link = current_app.config["DOMAIN"].rstrip("/") + url_for(
            "main.newsletter_confirm", token=token
        )
        payload = {
            "from": current_app.config["RESEND_FROM_EMAIL"],
            "to": [email],
            "subject": "Confirma a tua subscrição — HIM Dub Festival",
            "html": render_template("emails/newsletter_confirm.html", link=link),
            "text": "HIM Dub Festival — confirma a tua subscrição: "
            + link
            + "\nSe não pediste esta subscrição, ignora este email.",
        }
        enqueue(
            "email",
            payload,
            "newsletter-confirm:" + str(subscriber.id) + ":" + subscriber.nonce,
        )
        db.session.commit()
    flash(
        "Se necessário, receberás um email para confirmar a subscrição. Verifica também a pasta de spam.",
        "success",
    )
    return redirect(url_for("main.home"))


def subscriber_from_token(token, action):
    try:
        data = serializer().loads(
            token, max_age=48 * 3600 if action == "confirm" else 365 * 24 * 3600
        )
    except (BadSignature, SignatureExpired):
        abort(410, description="Este link expirou ou não é válido.")
    subscriber = db.session.get(NewsletterSubscriber, data.get("id"))
    if (
        not subscriber
        or data.get("nonce") != subscriber.nonce
        or data.get("action") != action
    ):
        abort(410, description="Este link já não está disponível.")
    return subscriber


def init_newsletter(main, bp, admin_required):
    @main.route("/newsletter/confirm", methods=["GET", "POST"])
    def newsletter_confirm():
        token = request.values.get("token", "")
        subscriber = subscriber_from_token(token, "confirm")
        done = False
        if request.method == "POST":
            subscriber.confirmed_at = datetime.utcnow()
            subscriber.unsubscribed_at = None
            subscriber.consent_text = CONSENT
            enqueue(
                "newsletter_sync",
                {"subscriber_id": subscriber.id},
                str(subscriber.id) + ":" + subscriber.nonce,
            )
            db.session.commit()
            done = True
        return render_template(
            "newsletter_result.html",
            done=done,
            action="confirm",
            token=token,
            unsubscribe_url=(
                url_for(
                    "main.newsletter_unsubscribe",
                    token=serializer().dumps(
                        {
                            "id": subscriber.id,
                            "nonce": subscriber.nonce,
                            "action": "unsubscribe",
                        }
                    ),
                )
                if done
                else None
            ),
        )

    @main.route("/newsletter/unsubscribe", methods=["GET", "POST"])
    def newsletter_unsubscribe():
        token = request.values.get("token", "")
        subscriber = subscriber_from_token(token, "unsubscribe")
        done = False
        if request.method == "POST":
            subscriber.unsubscribed_at = datetime.utcnow()
            # An old confirmation email must not undo a later opt-out.
            subscriber.nonce = secrets.token_hex(24)
            enqueue(
                "newsletter_sync",
                {"subscriber_id": subscriber.id},
                str(subscriber.id) + ":unsubscribe:" + subscriber.nonce,
            )
            db.session.commit()
            done = True
        return render_template(
            "newsletter_result.html", done=done, action="unsubscribe", token=token
        )

    @bp.route("/newsletter")
    @admin_required
    def newsletter_admin():
        page = NewsletterSubscriber.query.order_by(
            NewsletterSubscriber.created_at.desc()
        ).paginate(
            page=request.args.get("page", 1, type=int), per_page=30, error_out=False
        )
        return render_template(
            "admin/newsletter.html",
            pagination=page,
            confirmed=NewsletterSubscriber.query.filter(
                NewsletterSubscriber.confirmed_at.isnot(None),
                NewsletterSubscriber.unsubscribed_at.is_(None),
            ).count(),
            total=NewsletterSubscriber.query.count(),
            failures=WebsiteJob.query.filter(
                WebsiteJob.status.in_(("failed", "review"))
            ).count(),
            configured=bool(current_app.config.get("RESEND_NEWSLETTER_SEGMENT_ID")),
        )
