"""Versioned sales settings. Checkout and staff changes share the product row lock."""

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select, func, or_, and_
from extensions import db
from models import Product, TicketConfiguration, Ticket, Order, OrderItem

RETAIL_SLUGS = (
    "bilhete-geral",
    "full-pass-caravan",
    "passe-caravan",
    "bilhete-familia",
    "bilhete-fim-de-semana",
    "bilhete-acessibilidade",
)
LISBON = ZoneInfo("Europe/Lisbon")


def configuration(slug):
    # Scalar reads avoid stale ORM identities after waiting for a checkout lock.
    raw = db.session.execute(
        select(TicketConfiguration.payload)
        .join(Product, TicketConfiguration.product_id == Product.id)
        .where(Product.slug == slug)
    ).scalar_one_or_none()
    return json.loads(raw) if raw else None


def configured_tiers(slug):
    from batches import TICKET_BATCHES

    data = configuration(slug)
    if data is None:
        return TICKET_BATCHES.get(slug, [])
    from datetime import date

    return [
        {
            **tier,
            "until": date.fromisoformat(tier["until"]) if tier.get("until") else None,
        }
        for tier in data.get("tiers", [])
    ]


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def within_period(data):
    now = now_utc()
    return (
        not data.get("starts_at") or now >= datetime.fromisoformat(data["starts_at"])
    ) and (not data.get("ends_at") or now < datetime.fromisoformat(data["ends_at"]))


def sale_available(slug):
    from batches import BUNDLES, TICKET_BATCHES, current_tier

    slugs = [slug] + BUNDLES.get(slug, [])
    for component in slugs:
        product = Product.query.filter_by(slug=component).first()
        if not product or not product.is_active:
            return False
        data = configuration(component)
        if data and not within_period(data):
            return False
        if remaining_capacity(component) == 0:
            return False
        if component in TICKET_BATCHES and current_tier(component) is None:
            return False
    return True


def tier_units(slug, tier_index):
    from batches import _slugs_counting_towards

    slugs = _slugs_counting_towards(slug)
    sold = (
        db.session.query(func.count(Ticket.id))
        .join(Order, Ticket.order_id == Order.id)
        .filter(
            Ticket.product_slug.in_(slugs),
            Ticket.batch_tier == tier_index,
            Order.status == "paid",
        )
        .scalar()
    )
    held = (
        db.session.query(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order, OrderItem.order_id == Order.id)
        .join(Product, OrderItem.product_id == Product.id)
        .filter(
            Product.slug.in_(slugs),
            OrderItem.batch_tier == tier_index,
            or_(
                and_(
                    Order.status == "pending", Order.reservation_expires_at > now_utc()
                ),
                Order.status == "processing",
            ),
        )
        .scalar()
    )
    return sold + held


def remaining_capacity(slug):
    from batches import _slugs_counting_towards, _reserved_count

    data = configuration(slug)
    if not data or data.get("capacity") is None:
        return None
    sold = (
        db.session.query(func.count(Ticket.id))
        .join(Order, Ticket.order_id == Order.id)
        .filter(
            Ticket.product_slug.in_(_slugs_counting_towards(slug)),
            Order.status == "paid",
        )
        .scalar()
    )
    return max(0, data["capacity"] - sold - _reserved_count(slug))


def website_image(slug, tier=None):
    data = configuration(slug) or {}
    tiers = data.get("tiers", [])
    if tier is None and tiers:
        from batches import current_tier

        active = current_tier(slug)
        tier = active[0] if active else None
    if tier and tier <= len(tiers):
        return tiers[tier - 1].get("image_url") or data.get("image_url", "")
    return data.get("image_url", "")


def local_input(raw):
    return (
        datetime.fromisoformat(raw)
        .replace(tzinfo=timezone.utc)
        .astimezone(LISBON)
        .strftime("%Y-%m-%dT%H:%M")
        if raw
        else ""
    )


def parse_date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo or parsed.second or parsed.microsecond:
            raise ValueError()
        aware = parsed.replace(tzinfo=LISBON)
        # Reject nonexistent/ambiguous clock times instead of silently shifting a sale.
        if (
            aware.astimezone(timezone.utc).astimezone(LISBON).replace(tzinfo=None)
            != parsed
            or aware.utcoffset() != parsed.replace(tzinfo=LISBON, fold=1).utcoffset()
        ):
            raise ValueError()
        return aware.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    except ValueError:
        abort(
            400,
            description="Data inválida ou ambígua. Escolhe outra hora de Portugal continental.",
        )


def amount(value):
    try:
        decimal = Decimal(value.replace(",", "."))
        cents = decimal * 100
        if (
            not decimal.is_finite()
            or cents != cents.to_integral_value()
            or not 50 <= cents <= 1_000_000
        ):
            raise ValueError()
        return int(cents)
    except (ValueError, InvalidOperation):
        abort(
            400,
            description="Indica um preço entre 0,50 € e 10 000 €, com até duas casas decimais.",
        )


def optional_limit(value):
    if not value:
        return None
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 1_000_000:
        abort(
            400,
            description="O limite deve ser um número inteiro positivo ou ficar vazio.",
        )
    return int(value)


def validate_period(data):
    if (
        data.get("starts_at")
        and data.get("ends_at")
        and data["starts_at"] >= data["ends_at"]
    ):
        abort(400, description="A data de fecho deve ser posterior à abertura.")


def init_ticket_sales(bp, admin_required):
    from cms import audit, image_input
    from batches import TICKET_BATCHES, BUNDLES, current_tier

    @bp.get("/ticket-sales")
    @admin_required
    def ticket_sales():
        products = (
            Product.query.filter(Product.slug.in_(RETAIL_SLUGS))
            .order_by(Product.sort_order, Product.id)
            .all()
        )
        return render_template(
            "admin/ticket_sales.html",
            products=products,
            tier_for=current_tier,
            available=sale_available,
            image_for=website_image,
        )

    @bp.route("/ticket-sales/<int:product_id>", methods=["GET", "POST"])
    @admin_required
    def edit_ticket_sale(product_id):
        query = Product.query.filter_by(id=product_id, category="ticket")
        product = (
            query.populate_existing().with_for_update().first_or_404()
            if request.method == "POST"
            else query.first_or_404()
        )
        if product.slug not in RETAIL_SLUGS:
            abort(404)
        row = db.session.get(TicketConfiguration, product.id)
        data = configuration(product.slug) or {
            "image_url": "",
            "capacity": None,
            "starts_at": None,
            "ends_at": None,
            "tiers": [
                {
                    **tier,
                    "until": tier["until"].isoformat() if tier.get("until") else None,
                    "label": (
                        ["Early Bird", "2nd batch", "3rd batch", "4th batch"][i]
                        if i < 4
                        else f"Lote {i+1}"
                    ),
                }
                for i, tier in enumerate(TICKET_BATCHES.get(product.slug, []))
            ],
        }
        if request.method == "POST":
            if request.form.get("revision", type=int) != (row.revision if row else 0):
                abort(
                    409,
                    description="Outra pessoa alterou este bilhete. Reabre a página antes de guardar.",
                )
            if request.form.get("confirm_changes") != "on":
                abort(
                    400,
                    description="Confirma que pretendes aplicar estas regras às novas compras.",
                )
            new = {
                "image_url": image_input(),
                "capacity": optional_limit(request.form.get("capacity", "")),
                "starts_at": parse_date(request.form.get("starts_at", "")),
                "ends_at": parse_date(request.form.get("ends_at", "")),
                "tiers": [],
            }
            validate_period(new)
            count = request.form.get("tier_count", type=int)
            if (
                count is None
                or not len(data["tiers"]) <= count <= 20
                or (product.slug not in TICKET_BATCHES and count)
            ):
                abort(
                    400,
                    description="Não é possível remover ou reordenar lotes já registados.",
                )
            for i in range(count):
                prefix = f"tier_{i}_"
                old = data["tiers"][i] if i < len(data["tiers"]) else {}
                limit = optional_limit(request.form.get(prefix + "limit", ""))
                tier = {
                    "label": request.form.get(prefix + "label", "").strip()[:80],
                    "price_cents": amount(request.form.get(prefix + "price", "")),
                    "limit": limit,
                    "until": old.get("until"),
                    "starts_at": parse_date(request.form.get(prefix + "starts_at", "")),
                    "ends_at": parse_date(request.form.get(prefix + "ends_at", "")),
                    "image_url": image_input(prefix + "image_url", prefix + "image"),
                }
                if old.get("real_limit") is not None:
                    tier["real_limit"] = limit
                    tier["limit"] = optional_limit(
                        request.form.get(prefix + "public_limit", "")
                    )
                    if limit is None:
                        abort(
                            400,
                            description="O lote com contagem histórica precisa de um limite efetivo.",
                        )
                if not tier["label"]:
                    abort(400, description="Indica o nome de cada lote.")
                validate_period(tier)
                if tier["ends_at"]:
                    tier["until"] = None  # The new timestamp replaces the legacy date.
                new["tiers"].append(tier)
            for field, limit in (
                ("name", 120),
                ("short_description", 255),
                ("description", 10000),
            ):
                for language in ("", "_en", "_fr"):
                    value = request.form.get(field + language, "").strip()
                    if len(value) > limit or (
                        field == "name" and not language and not value
                    ):
                        abort(400, description="Revê o nome e os textos do bilhete.")
                    setattr(product, field + language, value)
            if product.slug not in BUNDLES:
                product.price_cents = (
                    new["tiers"][0]["price_cents"]
                    if new["tiers"]
                    else amount(request.form.get("price", ""))
                )
            product.is_active = request.form.get("is_active") == "on"
            row = row or TicketConfiguration(product_id=product.id, revision=0)
            row.payload = json.dumps(new, ensure_ascii=False)
            row.revision += 1
            row.updated_at = now_utc()
            db.session.add(row)
            audit("ticket_sale_updated", product.id)
            db.session.commit()
            flash(
                "Bilhete atualizado. As encomendas já criadas mantêm os valores registados.",
                "success",
            )
            return redirect(url_for("admin.ticket_sales"))
        return render_template(
            "admin/edit_ticket_sale.html",
            product=product,
            settings=data,
            revision=row.revision if row else 0,
            local_input=local_input,
            can_add=product.slug in TICKET_BATCHES,
            bundle=product.slug in BUNDLES,
        )
