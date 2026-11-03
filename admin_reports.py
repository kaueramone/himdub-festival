"""Reports count individual issued tickets, never translated sale labels."""

import io
from datetime import datetime
from flask import abort, render_template, request, send_file
from sqlalchemy import func
from openpyxl import Workbook
from extensions import db
from festival_time import local_day_bounds_utc, to_local_time
from models import Order, OrderItem, Product, Ticket, EmailDelivery


def date_filters(query):
    start, end = request.args.get("start", ""), request.args.get("end", "")
    try:
        first = datetime.strptime(start, "%Y-%m-%d").date() if start else None
        last = datetime.strptime(end, "%Y-%m-%d").date() if end else None
    except ValueError:
        abort(400, description="Indica datas válidas.")
    if first and last and first > last:
        abort(400, description="A data de início deve ser anterior à data de fim.")
    if first:
        query = query.filter(Order.created_at >= local_day_bounds_utc(first)[0])
    if last:
        query = query.filter(Order.created_at < local_day_bounds_utc(last)[1])
    return query


def ticket_query():
    query = (
        Ticket.query.join(Order)
        .join(OrderItem, Ticket.order_item_id == OrderItem.id)
        .outerjoin(Product, OrderItem.product_id == Product.id)
    )
    query = date_filters(query)
    slug = request.args.get("type", "").strip()
    if slug:
        query = query.filter(
            func.coalesce(func.nullif(Ticket.product_slug, ""), Product.slug) == slug
        )
    status = request.args.get("state", "paid")
    if status not in ("paid", "comp", "cancelled", "refunded", "demo-paid", "all"):
        abort(400, description="Estado inválido.")
    if status != "all":
        query = query.filter(Order.status == status)
    return query


def ticket_summary(query):
    slug = func.coalesce(func.nullif(Ticket.product_slug, ""), Product.slug, "sem-tipo")
    names = {p.slug: p.name for p in Product.query.all()}
    rows = (
        query.with_entities(
            slug,
            func.count(Ticket.id),
            func.count(Ticket.checked_in_at),
            func.sum(OrderItem.price_cents),
        )
        .group_by(slug)
        .order_by(slug)
        .all()
    )
    return [
        dict(
            slug=s,
            name=names.get(s, s),
            quantity=n,
            checked_in=c,
            revenue=(v or 0) / 100,
        )
        for s, n, c, v in rows
    ]


def init_reports(bp, admin_required, write_sheet):
    @bp.route("/dashboard")
    @admin_required
    def dashboard():
        total = (
            db.session.query(
                func.count(Order.id), func.coalesce(func.sum(Order.total_cents), 0)
            )
            .filter(Order.status == "paid")
            .one()
        )
        tickets = (
            db.session.query(func.count(Ticket.id), func.count(Ticket.checked_in_at))
            .join(Order)
            .filter(Order.status == "paid")
            .one()
        )
        comps = (
            db.session.query(func.count(Ticket.id))
            .join(Order)
            .filter(Order.status == "comp")
            .scalar()
        )
        pending = Order.query.filter(
            Order.status.in_(("pending", "processing"))
        ).count()
        attention = EmailDelivery.query.filter(
            EmailDelivery.status.in_(("failed", "review"))
        ).count()
        return render_template(
            "admin/dashboard.html",
            paid_orders=total[0],
            revenue=total[1] / 100,
            sold=tickets[0],
            checked=tickets[1],
            comps=comps,
            pending=pending,
            attention=attention,
            recent=Order.query.order_by(Order.created_at.desc(), Order.id.desc())
            .limit(5)
            .all(),
        )

    @bp.route("/sales")
    @admin_required
    def sales():
        query = ticket_query()
        summary = ticket_summary(query)
        from sqlalchemy.orm import joinedload

        page = (
            query.options(joinedload(Ticket.order))
            .order_by(Order.created_at.desc(), Ticket.id.desc())
            .paginate(
                page=request.args.get("page", 1, type=int), per_page=30, error_out=False
            )
        )
        return render_template(
            "admin/sales.html",
            summary=summary,
            pagination=page,
            products=Product.query.filter_by(category="ticket")
            .order_by(Product.sort_order, Product.name)
            .all(),
            names={p.slug: p.name for p in Product.query.all()},
            total=sum(r["quantity"] for r in summary),
            checked=sum(r["checked_in"] for r in summary),
        )

    @bp.route("/sales/export")
    @admin_required
    def sales_export():
        from sqlalchemy.orm import joinedload

        query = ticket_query()
        summary = ticket_summary(query)
        rows = (
            query.options(joinedload(Ticket.order), joinedload(Ticket.order_item))
            .order_by(Order.created_at, Ticket.id)
            .all()
        )
        names = {p.slug: p.name for p in Product.query.all()}
        workbook = Workbook()
        write_sheet(
            workbook.active,
            "Resumo por tipo",
            [
                "Tipo",
                "Identificador",
                "Bilhetes (unidades)",
                "Check-ins",
                "Valor dos bilhetes (€)",
            ],
            [
                [r["name"], r["slug"], r["quantity"], r["checked_in"], r["revenue"]]
                for r in summary
            ],
        )
        write_sheet(
            workbook.create_sheet(),
            "Bilhetes individuais",
            [
                "ID Bilhete",
                "ID Encomenda",
                "Data da encomenda (Portugal)",
                "Estado",
                "Tipo",
                "Identificador",
                "Quantidade",
                "Lote",
                "Titular",
                "Email do titular",
                "Valor unitário (€)",
                "Check-in",
            ],
            [
                [
                    t.id,
                    t.order_id,
                    to_local_time(t.order.created_at).strftime("%d/%m/%Y %H:%M"),
                    t.order.status,
                    names.get(t.product_slug, t.product_name),
                    t.product_slug,
                    1,
                    t.batch_tier,
                    t.holder_name or t.order.name,
                    t.holder_email or t.order.email,
                    t.order_item.price_cents / 100,
                    (
                        to_local_time(t.checked_in_at).strftime("%d/%m/%Y %H:%M")
                        if t.checked_in_at
                        else ""
                    ),
                ]
                for t in rows
            ],
        )
        write_sheet(
            workbook.create_sheet(),
            "Critérios",
            ["Campo", "Valor"],
            [
                ["Início", request.args.get("start", "Todo o histórico")],
                ["Fim", request.args.get("end", "Todo o histórico")],
                ["Tipo", request.args.get("type", "Todos")],
                ["Estado", request.args.get("state", "paid")],
                [
                    "Período",
                    "Data de criação da encomenda, dias completos, Europe/Lisbon",
                ],
                [
                    "Contagem",
                    "Uma linha por QR emitido. Packs e bilhetes de família não equivalem ao número de pessoas.",
                ],
                [
                    "Valor",
                    "Preço do bilhete; exclui taxas da encomenda e outros produtos.",
                ],
            ],
        )
        output = io.BytesIO()
        workbook.save(output)
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name="himdub-bilhetes.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
