"""Paid holder changes; no resale marketplace or TicketSwap integration."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import re
import secrets
import hmac
from zoneinfo import ZoneInfo
import stripe
from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.exc import IntegrityError
from auth import check_login_throttle
from extensions import db
from models import TicketTransfer, Ticket, Order, Setting, AuditEvent
from website_jobs import enqueue


def policy():
    settings = {
        s.key: s.value
        for s in Setting.query.filter(
            Setting.key.in_(
                ("transfer_enabled", "transfer_fee_cents", "transfer_deadline")
            )
        )
    }
    return {
        "enabled": settings.get("transfer_enabled") == "true",
        "fee_cents": int(settings.get("transfer_fee_cents", "500")),
        "deadline": datetime.fromisoformat(
            settings.get("transfer_deadline", "2027-08-21T00:00:00")
        )
        .replace(tzinfo=ZoneInfo("Europe/Lisbon"))
        .astimezone(timezone.utc)
        .replace(tzinfo=None),
    }


def owner_email(ticket):
    return (ticket.holder_email or ticket.order.email).strip().lower()


def ticket_eligible(ticket, transfer=None):
    if ticket.product_slug in ("bilhete-familia", "bilhete-acessibilidade"):
        # These include companion/children data and eligibility conditions.
        # The organisation reviews their changes through the existing editor.
        return False
    if ticket.order.status != "paid" or ticket.checked_in_at:
        return False
    if transfer:
        return (
            hmac.compare_digest(
                hashlib.sha256(ticket.code.encode()).hexdigest(),
                transfer.original_code_hash,
            )
            and owner_email(ticket) == transfer.original_email
        )
    return True


def transfer_from_token(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        abort(404)
    transfer = TicketTransfer.query.filter_by(
        token_hash=hashlib.sha256(token.encode()).hexdigest()
    ).first()
    if not transfer or (
        transfer.expires_at <= datetime.utcnow()
        and transfer.status not in ("completed", "refund_pending", "refunded")
    ):
        abort(410, description="O link expirou. Pede um novo link de transferência.")
    return transfer


def lock_transfer(identifier):
    transfer = db.session.get(TicketTransfer, identifier)
    if not transfer:
        return None, None
    ticket = db.session.get(Ticket, transfer.ticket_id)
    Order.query.filter_by(id=ticket.order_id).with_for_update().one()
    ticket = (
        Ticket.query.filter_by(id=ticket.id).populate_existing().with_for_update().one()
    )
    transfer = (
        TicketTransfer.query.filter_by(id=identifier)
        .populate_existing()
        .with_for_update()
        .one()
    )
    return transfer, ticket


def refresh_expired_checkout(transfer):
    """Release an abandoned request only after Stripe confirms expiration."""
    if (
        transfer.status != "pending_payment"
        or not transfer.stripe_session_id
        or not transfer.checkout_expires_at
        or transfer.checkout_expires_at > datetime.utcnow()
    ):
        return
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    try:
        checkout = stripe.checkout.Session.retrieve(transfer.stripe_session_id)
        if checkout.get("status") == "expired":
            transfer.status = "cancelled"
            transfer.active_ticket_id = None
            db.session.commit()
        elif checkout.get("payment_status") == "paid":
            handle_transfer_event("checkout.session.completed", checkout)
    except stripe.error.StripeError:
        db.session.rollback()


def handle_transfer_event(event_type, obj):
    identifier = (obj.get("metadata") or {}).get("ticket_transfer_id")
    transfer = TicketTransfer.query.filter_by(
        stripe_session_id=obj.get("id", "")
    ).first()
    if not transfer and identifier:
        transfer = db.session.get(TicketTransfer, identifier)
    if not transfer:
        return False
    transfer, ticket = lock_transfer(transfer.id)
    if transfer.stripe_session_id and transfer.stripe_session_id != obj.get("id"):
        raise ValueError("Transfer session mismatch")
    if event_type in (
        "checkout.session.expired",
        "checkout.session.async_payment_failed",
    ):
        if transfer.status == "pending_payment":
            transfer.status = "cancelled"
            transfer.active_ticket_id = None
            db.session.commit()
        return True
    if event_type not in (
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    ):
        return True
    if (
        obj.get("amount_total") != transfer.fee_cents
        or obj.get("currency") != "eur"
        or obj.get("mode") != "payment"
        or bool(obj.get("livemode"))
        != current_app.config["STRIPE_SECRET_KEY"].startswith(("sk_live_", "rk_live_"))
    ):
        raise ValueError("Transfer payment mismatch")
    if obj.get("payment_status") != "paid" or transfer.status in (
        "completed",
        "refund_pending",
        "refunded",
    ):
        db.session.rollback()
        return True
    transfer.stripe_session_id = obj["id"]
    transfer.payment_intent = obj.get("payment_intent", "")
    if not transfer.new_email or not transfer.new_name:
        raise ValueError("Transfer not prepared")
    # Stripe enforces checkout expiry. A delayed webhook must still complete
    # a payment made within the checkout window that was agreed beforehand.
    if not ticket_eligible(ticket, transfer):
        transfer.status = "refund_pending"
        transfer.active_ticket_id = None
        enqueue("transfer_refund", {"transfer_id": transfer.id}, transfer.id)
        db.session.commit()
        return True
    old_code = ticket.code
    from ticketing import _new_ticket_code

    ticket.code = _new_ticket_code()
    ticket.previous_codes = ((ticket.previous_codes or "") + "\n" + old_code).strip()
    ticket.holder_name = transfer.new_name
    ticket.holder_email = transfer.new_email
    transfer.status = "completed"
    transfer.completed_at = datetime.utcnow()
    transfer.active_ticket_id = None
    from outbox import enqueue_order_emails

    enqueue_order_emails(
        ticket.order,
        event_id="transfer-" + transfer.id,
        recipients={transfer.new_email},
    )
    db.session.add(
        AuditEvent(
            actor=transfer.original_email,
            action="ticket_transferred",
            target=str(ticket.id),
        )
    )
    enqueue(
        "email",
        {
            "from": current_app.config["RESEND_FROM_EMAIL"],
            "to": [transfer.original_email],
            "subject": "Transferência de titular concluída — HIM Dub Festival",
            "text": "A transferência do teu bilhete foi concluída. O QR antigo foi invalidado. O novo titular recebe o bilhete no email que indicaste.",
        },
        "transfer-notice:" + transfer.id,
    )
    db.session.commit()
    return True


def init_transfers(main, bp, admin_required):
    @main.route("/ticket-transfer", methods=["GET", "POST"])
    def ticket_transfer():
        rules = policy()
        available = rules["enabled"] and datetime.utcnow() < rules["deadline"]
        if request.method == "POST":
            if not available:
                abort(
                    400, description="O serviço de transferência não está disponível."
                )
            email = request.form.get("email", "").strip().lower()[:180]
            code = request.form.get("code", "").strip().upper()[:40]
            check_login_throttle("ticket-transfer:" + email)
            ticket = Ticket.query.filter_by(code=code).first()
            if ticket and ticket_eligible(ticket) and owner_email(ticket) == email:
                Order.query.filter_by(id=ticket.order_id).with_for_update().one()
                ticket = (
                    Ticket.query.filter_by(id=ticket.id)
                    .populate_existing()
                    .with_for_update()
                    .one()
                )
                active = (
                    TicketTransfer.query.filter_by(active_ticket_id=ticket.id)
                    .with_for_update()
                    .first()
                )
                if active:
                    refresh_expired_checkout(active)
                    if active.status in (
                        "cancelled",
                        "completed",
                        "refund_pending",
                        "refunded",
                    ):
                        active = None
                if (
                    active
                    and active.expires_at < datetime.utcnow()
                    and active.status == "awaiting_email"
                ):
                    active.active_ticket_id = None
                    active.status = "cancelled"
                    db.session.flush()
                    active = None
                if (
                    not active
                    and ticket_eligible(ticket)
                    and owner_email(ticket) == email
                ):
                    token = secrets.token_urlsafe(32)
                    transfer = TicketTransfer(
                        id=secrets.token_hex(16),
                        ticket_id=ticket.id,
                        active_ticket_id=ticket.id,
                        token_hash=hashlib.sha256(token.encode()).hexdigest(),
                        original_code_hash=hashlib.sha256(
                            ticket.code.encode()
                        ).hexdigest(),
                        original_email=email,
                        fee_cents=rules["fee_cents"],
                        expires_at=min(
                            datetime.utcnow() + timedelta(hours=24), rules["deadline"]
                        ),
                    )
                    db.session.add(transfer)
                    link = current_app.config["DOMAIN"].rstrip("/") + url_for(
                        "main.transfer_accept", token=token
                    )
                    enqueue(
                        "email",
                        {
                            "from": current_app.config["RESEND_FROM_EMAIL"],
                            "to": [email],
                            "subject": "Confirmar troca de titular — HIM Dub Festival",
                            "text": "Foi pedida uma troca de titular do teu bilhete. Para indicar o novo titular e pagar a taxa de serviço, abre: "
                            + link
                            + "\nSe não fizeste este pedido, não abras o link. O teu bilhete mantém-se válido.",
                        },
                        "transfer-link:" + transfer.id,
                    )
                    try:
                        db.session.commit()
                    except IntegrityError:
                        db.session.rollback()
            flash(
                "Se os dados corresponderem a um bilhete elegível, receberás um link no email do titular atual.",
                "success",
            )
            return redirect(url_for("main.ticket_transfer"))
        return render_template(
            "transfer.html", rules=rules, available=available, transfer=None
        )

    @main.route("/ticket-transfer/accept", methods=["GET", "POST"])
    def transfer_accept():
        token = request.values.get("token", "")
        transfer = transfer_from_token(token)
        rules = policy()
        if request.method == "GET":
            refresh_expired_checkout(transfer)
        if request.method == "POST":
            check_login_throttle("transfer-accept:" + transfer.id)
            transfer, ticket = lock_transfer(transfer.id)
            if (
                not rules["enabled"]
                or datetime.utcnow() + timedelta(minutes=31) >= rules["deadline"]
                or not ticket_eligible(ticket, transfer)
            ):
                abort(
                    409,
                    description="Este bilhete já não está disponível para transferência.",
                )
            if transfer.status not in ("awaiting_email", "pending_payment"):
                abort(409)
            if transfer.status == "awaiting_email":
                name = request.form.get("name", "").strip()
                email = request.form.get("email", "").strip().lower()
                if (
                    not name
                    or len(name) > 120
                    or len(email) > 180
                    or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
                ):
                    abort(
                        400,
                        description="Indica o nome e email válidos do novo titular.",
                    )
                if request.form.get("accept_terms") != "yes":
                    abort(400, description="Confirma as condições da transferência.")
                transfer.new_name = name
                transfer.new_email = email
                transfer.status = "pending_payment"
                transfer.checkout_expires_at = min(
                    datetime.utcnow() + timedelta(minutes=35), rules["deadline"]
                )
                db.session.commit()
            stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
            try:
                checkout = stripe.checkout.Session.create(
                    mode="payment",
                    payment_method_types=["card"],
                    line_items=[
                        {
                            "quantity": 1,
                            "price_data": {
                                "currency": "eur",
                                "unit_amount": transfer.fee_cents,
                                "product_data": {
                                    "name": "HIM Dub — troca de titular de 1 bilhete"
                                },
                            },
                        }
                    ],
                    customer_email=transfer.original_email,
                    metadata={"ticket_transfer_id": transfer.id},
                    success_url=current_app.config["DOMAIN"].rstrip("/")
                    + url_for("main.transfer_accept", token=token)
                    + "&session_id={CHECKOUT_SESSION_ID}",
                    cancel_url=current_app.config["DOMAIN"].rstrip("/")
                    + url_for("main.transfer_accept", token=token),
                    expires_at=int(
                        transfer.checkout_expires_at.replace(
                            tzinfo=timezone.utc
                        ).timestamp()
                    ),
                    idempotency_key="ticket-transfer-" + transfer.id,
                )
                transfer.stripe_session_id = checkout.id
                db.session.commit()
            except stripe.error.StripeError:
                db.session.rollback()
                abort(
                    502,
                    description="Não foi possível abrir o pagamento. Tenta novamente neste mesmo link.",
                )
            return redirect(checkout.url, code=303)
        stripe_session = request.args.get("session_id", "")
        if (
            stripe_session
            and transfer.status == "pending_payment"
            and stripe_session == transfer.stripe_session_id
        ):
            stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
            try:
                confirmed = stripe.checkout.Session.retrieve(stripe_session)
                handle_transfer_event("checkout.session.completed", confirmed)
            except stripe.error.StripeError:
                db.session.rollback()
        return render_template(
            "transfer.html", rules=rules, available=True, transfer=transfer, token=token
        )

    @bp.route("/transfers", methods=["GET", "POST"])
    @admin_required
    def transfers_admin():
        if request.method == "POST":
            try:
                amount = Decimal(request.form.get("fee", "5").replace(",", "."))
                if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
                    raise ValueError("Invalid cents")
                cents = int(amount * 100)
                deadline = datetime.fromisoformat(request.form.get("deadline", ""))
            except (ValueError, OverflowError, InvalidOperation):
                abort(400, description="Indica um valor e uma data válidos.")
            if cents < 50 or cents > 10000:
                abort(400, description="A taxa deve ser entre 0,50 € e 100 €.")
            for key, value in [
                (
                    "transfer_enabled",
                    "true" if request.form.get("enabled") == "on" else "false",
                ),
                ("transfer_fee_cents", str(cents)),
                ("transfer_deadline", deadline.isoformat()),
            ]:
                setting = db.session.get(Setting, key) or Setting(key=key)
                setting.value = value
                db.session.add(setting)
            db.session.add(
                AuditEvent(
                    actor=session.get("admin_name"), action="transfer_policy_updated"
                )
            )
            db.session.commit()
            flash("Condições de transferência guardadas.", "success")
            return redirect(url_for("admin.transfers_admin"))
        rules = policy()
        return render_template(
            "admin/transfers.html",
            rules=rules,
            recent=TicketTransfer.query.order_by(TicketTransfer.created_at.desc())
            .limit(50)
            .all(),
        )
