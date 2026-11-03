"""Verified email access to a buyer's orders and only the tickets they still own."""

from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import re
import secrets
import io
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from auth import check_login_throttle
from extensions import db
from models import (
    CustomerAccount,
    CustomerAccessToken,
    CustomerRequest,
    Order,
    Ticket,
    AuditEvent,
)
from website_jobs import enqueue

customer_bp = Blueprint("customer", __name__, url_prefix="/account")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def email_value(value):
    value = value.strip().lower()
    if len(value) > 180 or not re.fullmatch(
        r"[a-z0-9.!#$%&*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}", value
    ):
        abort(400, description="Indica um endereço de email válido.")
    return value


def current_customer():
    identifier = session.get("customer_id")
    started = session.get("customer_started", 0)
    if (
        not identifier
        or not isinstance(started, (int, float))
        or datetime.now(timezone.utc).timestamp() - started > 12 * 3600
    ):
        return None
    account = db.session.get(CustomerAccount, identifier)
    return (
        account
        if account and account.session_version == session.get("customer_version")
        else None
    )


def customer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        g.customer = current_customer()
        if not g.customer:
            return redirect(url_for("customer.login"))
        return view(*args, **kwargs)

    return wrapped


def buyer_orders(account):
    return Order.query.filter(func.lower(func.trim(Order.email)) == account.email)


def owned_tickets(account):
    return Ticket.query.join(Order, Ticket.order_id == Order.id).filter(
        or_(
            func.lower(func.trim(Ticket.holder_email)) == account.email,
            and_(
                or_(
                    Ticket.holder_email.is_(None), func.trim(Ticket.holder_email) == ""
                ),
                func.lower(func.trim(Order.email)) == account.email,
            ),
        )
    )


def owned_ticket(identifier):
    return owned_tickets(g.customer).filter(Ticket.id == identifier).first_or_404()


def downloadable(ticket):
    return ticket.order.status == "paid" and not ticket.checked_in_at


@customer_bp.route("/login", methods=["GET", "POST"])
def login():
    sent = False
    if request.method == "POST":
        email = email_value(request.form.get("email", ""))
        check_login_throttle("customer:" + email)
        now = datetime.utcnow()
        recent = CustomerAccessToken.query.filter(
            CustomerAccessToken.email == email,
            CustomerAccessToken.created_at > now - timedelta(minutes=1),
        ).first()
        if not recent:
            token = secrets.token_urlsafe(32)
            db.session.add(
                CustomerAccessToken(
                    token_hash=digest(token),
                    email=email,
                    expires_at=now + timedelta(minutes=20),
                )
            )
            link = current_app.config["DOMAIN"].rstrip("/") + url_for(
                "customer.access", token=token
            )
            enqueue(
                "email",
                {
                    "from": current_app.config["RESEND_FROM_EMAIL"],
                    "to": [email],
                    "subject": "O teu acesso privado — HIM Dub Festival",
                    "html": render_template("emails/customer_access.html", link=link),
                    "text": "Entra na tua área de cliente do HIM Dub Festival: "
                    + link
                    + "\nLink privado, válido por 20 minutos e uma única utilização. Se não pediste acesso, ignora este email.",
                },
                "customer-access:" + digest(token),
            )
            db.session.commit()
        sent = True
    return render_template("customer/login.html", sent=sent)


@customer_bp.route("/access/<token>", methods=["GET", "POST"])
def access(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        abort(404)
    query = CustomerAccessToken.query.filter_by(token_hash=digest(token))
    access_token = (
        query.populate_existing().with_for_update().first()
        if request.method == "POST"
        else query.first()
    )
    now = datetime.utcnow()
    if not access_token or access_token.used_at or access_token.expires_at <= now:
        abort(
            410, description="O link expirou ou já foi utilizado. Pede um novo acesso."
        )
    if request.method == "POST":
        account = CustomerAccount.query.filter_by(email=access_token.email).first()
        if not account:
            account = CustomerAccount(email=access_token.email)
            try:
                with db.session.begin_nested():
                    db.session.add(account)
                    db.session.flush()
            except IntegrityError:
                account = CustomerAccount.query.filter_by(
                    email=access_token.email
                ).one()
        access_token.used_at = now
        db.session.commit()
        session.update(
            customer_id=account.id,
            customer_version=account.session_version,
            customer_started=now.replace(tzinfo=timezone.utc).timestamp(),
            csrf_token=secrets.token_urlsafe(32),
        )
        return redirect(url_for("customer.dashboard"))
    return render_template("customer/access.html")


@customer_bp.post("/logout")
@customer_required
def logout():
    # Revoke all customer sessions, without changing an unrelated staff session.
    g.customer.session_version += 1
    db.session.commit()
    for key in ("customer_id", "customer_version", "customer_started"):
        session.pop(key, None)
    return redirect(url_for("customer.login"))


@customer_bp.get("/")
@customer_required
def dashboard():
    return render_template(
        "customer/dashboard.html",
        account=g.customer,
        orders=buyer_orders(g.customer)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(5)
        .all(),
        tickets=owned_tickets(g.customer).order_by(Ticket.id.desc()).limit(6).all(),
        requests=CustomerRequest.query.filter_by(customer_id=g.customer.id)
        .order_by(CustomerRequest.created_at.desc(), CustomerRequest.id.desc())
        .limit(5)
        .all(),
        downloadable=downloadable,
    )


@customer_bp.get("/orders")
@customer_required
def orders():
    page = (
        buyer_orders(g.customer)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .paginate(
            page=request.args.get("page", 1, type=int), per_page=20, error_out=False
        )
    )
    return render_template("customer/orders.html", account=g.customer, pagination=page)


@customer_bp.get("/orders/<int:identifier>")
@customer_required
def order_detail(identifier):
    order = (
        buyer_orders(g.customer)
        .filter(Order.id == identifier)
        .options(selectinload(Order.items))
        .first_or_404()
    )
    tickets = owned_tickets(g.customer).filter(Ticket.order_id == order.id).all()
    return render_template(
        "customer/order.html",
        account=g.customer,
        order=order,
        tickets=tickets,
        downloadable=downloadable,
    )


@customer_bp.get("/tickets")
@customer_required
def tickets():
    page = (
        owned_tickets(g.customer)
        .order_by(Ticket.id.desc())
        .paginate(
            page=request.args.get("page", 1, type=int), per_page=20, error_out=False
        )
    )
    return render_template(
        "customer/tickets.html",
        account=g.customer,
        pagination=page,
        downloadable=downloadable,
    )


@customer_bp.get("/tickets/<int:identifier>/download")
@customer_required
def download(identifier):
    ticket = owned_ticket(identifier)
    if not downloadable(ticket):
        abort(409, description="Este bilhete não está disponível para descarregar.")
    from ticket_image import generate_ticket_image_bytes

    # A holder receiving a ticket must not learn the original buyer's name.
    class DownloadView:
        holder_name = ticket.holder_name or (
            ticket.order.name
            if ticket.order.email.strip().lower() == g.customer.email
            else "Titular do bilhete"
        )

        def __getattr__(self, name):
            return getattr(ticket, name)

    raw = generate_ticket_image_bytes(
        DownloadView(), current_app.config["DOMAIN"], lang_code="pt"
    )
    if raw:
        return send_file(
            io.BytesIO(raw),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"HIM-Dub-bilhete-{ticket.id}.pdf",
        )
    from ticketing import ticket_qr_png_bytes

    return send_file(
        io.BytesIO(ticket_qr_png_bytes(ticket, current_app.config["DOMAIN"])),
        mimetype="image/png",
        as_attachment=True,
        download_name=f"HIM-Dub-bilhete-{ticket.id}-QR.png",
    )


@customer_bp.route("/profile", methods=["GET", "POST"])
@customer_required
def profile():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        if (
            len(name) > 120
            or len(phone) > 40
            or (phone and not re.fullmatch(r"[+0-9() .-]+", phone))
        ):
            abort(400, description="Revê o nome e o contacto telefónico.")
        g.customer.name, g.customer.phone = name, phone
        db.session.commit()
        flash(
            "Dados de contacto guardados. Os dados das compras anteriores não foram alterados.",
            "success",
        )
        return redirect(url_for("customer.profile"))
    return render_template("customer/profile.html", account=g.customer)


@customer_bp.route("/tickets/<int:identifier>/request", methods=["GET", "POST"])
@customer_required
def ticket_request(identifier):
    ticket = owned_ticket(identifier)
    if not downloadable(ticket):
        abort(
            409,
            description="Só podes pedir alterações a um bilhete pago que ainda não foi utilizado.",
        )
    from transfers import policy, ticket_eligible

    rules = policy()
    automatic = (
        rules["enabled"]
        and datetime.utcnow() < rules["deadline"]
        and ticket_eligible(ticket)
    )
    if request.method == "POST":
        check_login_throttle("customer-request:" + g.customer.email)
        # Same order -> ticket lock order as payment and holder transfer handlers.
        Order.query.filter_by(id=ticket.order_id).with_for_update().one()
        ticket = (
            owned_tickets(g.customer)
            .filter(Ticket.id == identifier)
            .populate_existing()
            .with_for_update()
            .first_or_404()
        )
        if not downloadable(ticket):
            abort(409)
        if CustomerRequest.query.filter_by(active_ticket_id=ticket.id).first():
            abort(409, description="Já existe um pedido em análise para este bilhete.")
        kind = request.form.get("kind", "")
        name = request.form.get("name", "").strip()
        message = request.form.get("message", "").strip()
        if (
            kind not in ("name_correction", "holder_change", "help")
            or len(name) > 120
            or not 5 <= len(message) <= 2000
            or (kind != "help" and not name)
        ):
            abort(
                400,
                description="Indica o nome pretendido e explica o pedido entre 5 e 2000 caracteres.",
            )
        email = email_value(request.form.get("email", "") or g.customer.email)
        db.session.add(
            CustomerRequest(
                customer_id=g.customer.id,
                ticket_id=ticket.id,
                active_ticket_id=ticket.id,
                code_hash=digest(ticket.code),
                kind=kind,
                requested_name=name,
                requested_email=email,
                message=message,
            )
        )
        db.session.commit()
        flash(
            "Pedido enviado à organização. O bilhete mantém-se como está até à análise. Podes acompanhar a resposta na tua conta.",
            "success",
        )
        return redirect(url_for("customer.requests_list"))
    return render_template(
        "customer/request.html",
        account=g.customer,
        ticket=ticket,
        automatic=automatic,
        fee=rules["fee_cents"],
    )


@customer_bp.get("/requests")
@customer_required
def requests_list():
    page = (
        CustomerRequest.query.filter_by(customer_id=g.customer.id)
        .order_by(CustomerRequest.created_at.desc(), CustomerRequest.id.desc())
        .paginate(
            page=request.args.get("page", 1, type=int), per_page=20, error_out=False
        )
    )
    return render_template(
        "customer/requests.html", account=g.customer, pagination=page
    )


def init_customer_admin(bp, admin_required):
    @bp.route("/customer-requests", methods=["GET", "POST"])
    @admin_required
    def customer_requests():
        if request.method == "POST":
            item = (
                CustomerRequest.query.filter_by(id=request.form.get("id", type=int))
                .with_for_update()
                .first_or_404()
            )
            state = request.form.get("status", "")
            response = request.form.get("response", "").strip()
            if (
                item.status != "pending"
                or state not in ("resolved", "rejected")
                or not 5 <= len(response) <= 2000
            ):
                abort(
                    400,
                    description="Indica o resultado e uma resposta entre 5 e 2000 caracteres.",
                )
            # Resolve the support request only: existing ticket tools perform changes.
            item.status, item.response, item.resolved_at = (
                state,
                response,
                datetime.utcnow(),
            )
            item.active_ticket_id = None
            db.session.add(
                AuditEvent(
                    actor=session.get("admin_name", "staff"),
                    action="customer_request_" + state,
                    target=str(item.id),
                )
            )
            db.session.commit()
            flash("Resposta guardada e disponível na conta do cliente.", "success")
            return redirect(url_for("admin.customer_requests"))
        page = (
            CustomerRequest.query.options(
                selectinload(CustomerRequest.customer),
                selectinload(CustomerRequest.ticket),
            )
            .order_by(CustomerRequest.created_at.desc())
            .paginate(
                page=request.args.get("page", 1, type=int), per_page=20, error_out=False
            )
        )
        return render_template("admin/customer_requests.html", pagination=page)
