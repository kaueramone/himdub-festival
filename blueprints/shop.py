"""Loja: bilhetes, glamping e merchandise + carrinho + checkout.

O carrinho vive na SESSAO do browser (nao precisa de login). Cada linha do
carrinho e guardada como "chave -> quantidade", em que a chave combina o id
do produto com a variante escolhida (ex: "7:M" = produto 7, tamanho M).
"""

import re
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

from flask import (
    Blueprint, render_template, request, redirect, session, flash,
    jsonify, current_app, abort,
)
from flask_babel import gettext as _
from extensions import db
from models import Product, Order, OrderItem, encode_ticket_names
from countries import COUNTRIES
from ticketing import generate_tickets_for_order
from emailing import send_low_stock_alert_email
from i18n import localized_url_for as url_for, current_lang
from batches import (
    resolve_batch_tier_for_order_item, remaining_in_current_tier, batch_tier_index,
    BUNDLES, TICKET_BATCHES, all_tiers_with_status, check_and_flag_low_stock_alert,
)
from fees import cart_processing_fee_cents, order_processing_fee_cents
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from security import local_redirect

shop_bp = Blueprint("shop", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# NIF portugues: 9 digitos -- so validamos o formato (nao o digito de
# controlo), para nao recusar um NIF valido por causa de um bug nosso a
# calcular o checksum. E um campo opcional (so para quem quer fatura com
# numero de contribuinte).
NIF_RE = re.compile(r"^\d{9}$")

# Only low-stock notifications use this best-effort pool. Ticket confirmations
# are persisted transactionally in the email outbox.
_alert_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stock-alert")


def _send_low_stock_alert_in_background(app, product_slug, tier_idx, remaining):
    """Corre no _email_executor, tal como o email de confirmação -- não
    deve atrasar a confirmação da compra do cliente por causa de um email
    interno para a organização."""
    with app.app_context():
        label = _tier_batch_labels().get(tier_idx, f"Lote {tier_idx}")
        next_label = _tier_batch_labels().get(tier_idx + 1, "")
        subject = f"⚠️ HIM Dub Festival — faltam {remaining} bilhetes para o {label} acabar"
        html = (
            f"<p>Faltam apenas <b>{remaining}</b> bilhetes reais (pagos a sério no Stripe) "
            f"para o <b>{label}</b> do Full Pass esgotar"
            + (f", e o <b>{next_label}</b> abrir a seguir" if next_label else "")
            + ".</p>"
            "<p>Vê o estado atual em /admin/orders.</p>"
        )
        try:
            send_low_stock_alert_email(subject, html, current_app.config)
        except Exception:
            current_app.logger.exception(
                "Falha inesperada a enviar o alerta de stock baixo (%s, lote %s)",
                product_slug, tier_idx,
            )


def _check_low_stock_alerts(order):
    """Chamado depois de uma encomenda ficar paga -- se algum bilhete desta
    encomenda pertence a um produto com lotes (ver batches.py) e isso fez
    o lote atual cair a LOW_STOCK_ALERT_THRESHOLD unidades reais ou menos,
    avisa a organização (banner no admin + email), só a primeira vez para
    cada lote (ver check_and_flag_low_stock_alert)."""
    slugs = {t.product_slug for t in order.tickets if t.product_slug in TICKET_BATCHES}
    if not slugs:
        return
    app = current_app._get_current_object()
    for slug in slugs:
        result = check_and_flag_low_stock_alert(slug)
        if result:
            tier_idx, remaining = result
            _alert_executor.submit(_send_low_stock_alert_in_background, app, slug, tier_idx, remaining)


def _finalize_paid_order(order):
    """Lock the order and atomically persist its tickets and email jobs.

    A failed write rolls back payment status too. Retries repair missing
    units; email delivery runs separately from the browser/webhook request.
    """
    try:
        # PostgreSQL serialises webhook/return retries for the same order.
        order = Order.query.filter_by(id=order.id).with_for_update().populate_existing().one()
        if order.status not in ("paid", "demo-paid", "comp"):
            raise ValueError("Order has no admission entitlement")
        db.session.expire(order, ["tickets"])
        created = generate_tickets_for_order(order)
        if created:
            order.email_sent = False
        if (not order.email_sent and current_app.config.get("EMAILS_LIVE")
                and order.status in ("paid", "comp")):
            from outbox import enqueue_order_emails
            enqueue_order_emails(order)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    _check_low_stock_alerts(order)


def _order_token(order_id):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="order-confirmation").dumps(order_id)


def _success_url(order):
    return url_for("shop.checkout_success", order_id=order.id, token=_order_token(order.id))


def _confirm_stripe_payment(obj):
    order = Order.query.filter_by(stripe_session_id=obj.get("id", "")).with_for_update().populate_existing().first()
    if not order:
        return
    if (obj.get("mode") != "payment" or obj.get("currency") != "eur"
            or obj.get("amount_total") != order.total_cents
            or obj.get("livemode") is not current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")):
        raise ValueError("Stripe payment does not match the order")
    if obj.get("payment_status") != "paid":
        if order.status == "pending":
            order.status = "processing"
            order.reservation_expires_at = None
            db.session.commit()
        return
    if order.status not in ("pending", "processing", "paid"):
        return
    order.status = "paid"
    nif = _extract_stripe_custom_field(obj, "nif")
    if nif and NIF_RE.fullmatch(nif):
        order.nif = nif
    _finalize_paid_order(order)


# Símbolo Adinkra associado a cada bilhete, usado no ícone da página de
# bilhetes -- os mesmos símbolos que já aparecem nos templates gráficos dos
# bilhetes (ver ticket_image.py), para ficar tudo consistente. Ajusta/
# acrescenta aqui se criares mais tipos de bilhete no seed.py. Ficheiros em
# static/img/icons/<nome>.png.
TICKET_ICONS = {
    "bilhete-geral": "akoma",            # Akoma — compromisso total com o festival
    "bilhete-acessibilidade": "bese-saka",  # Bese Saka — símbolo próprio do novo template
    "bilhete-fim-de-semana": "bi-nka-bi",
    "bilhete-familia": "sankofa",        # Sankofa — trazes a família, olhas para o percurso
    "passe-caravan": "fofo",
    "full-pass-caravan": "akoma",        # pacote Full Pass + Caravan -- segue o icone do Full Pass
}

# Símbolo usado a partir do 2º lote de cada bilhete com lotes (ver
# batches.py) -- None = mantém o mesmo ícone em todos os lotes.
TICKET_ICONS_BY_TIER = {
    ("bilhete-geral", 2): "duage",
    ("bilhete-geral", 3): "ananse",
    ("bilhete-geral", 4): "dwenin",
    ("full-pass-caravan", 2): "duage",
    ("full-pass-caravan", 3): "ananse",
    ("full-pass-caravan", 4): "dwenin",
}

# Nome do símbolo Adinkra de cada lote do Full Pass -- é um nome próprio,
# por isso é igual nas 3 línguas (não precisa de tradução). Usado na página
# de bilhetes para mostrar, por baixo do preço/lote, qual o símbolo desse
# lote (ex: "AKOMA", "DUAGE").
TIER_SYMBOL_NAMES = {
    ("bilhete-geral", 1): "Akoma",
    ("bilhete-geral", 2): "Duage",
    ("bilhete-geral", 3): "Ananse Ntontan",
    ("bilhete-geral", 4): "Djwennimmen",
}


def _tiered_component(slug):
    """Para um pacote (ver batches.py BUNDLES), devolve o slug do seu
    componente com lotes (só há um, nos casos atuais); para um bilhete com
    lotes diretos, devolve o próprio slug; caso contrário None."""
    if slug in BUNDLES:
        for comp in BUNDLES[slug]:
            if comp in TICKET_BATCHES:
                return comp
        return None
    return slug


def _ticket_icon(slug):
    comp = _tiered_component(slug)
    tier = batch_tier_index(comp) if comp else None
    if tier:
        icon = TICKET_ICONS_BY_TIER.get((slug, tier))
        if icon:
            return icon
    return TICKET_ICONS.get(slug, "akoma")


def _tier_batch_labels():
    """Legenda curta de cada lote -- igual para qualquer bilhete que use
    lotes (por agora só o Full Pass). Fica sempre em inglês nas 3 línguas
    do site (decisão do João, tal como "Full Pass"/"Early Bird" -- ver
    templates/tickets.html), por isso NÃO passa por _() aqui."""
    return {
        1: "Early Bird",
        2: "2nd batch",
        3: "3rd batch",
        4: "4th batch",
    }


def _tier_rows(slug):
    """Lista de TODOS os lotes deste produto (ver batches.all_tiers_with_status),
    já com ícone, nome do símbolo, legenda traduzida e preço em euros --
    pronta para o template desenhar o lote ativo em destaque e os restantes
    (esgotados riscados / ainda por vir) numa lista mais pequena por baixo."""
    labels = _tier_batch_labels()
    rows = []
    for t in all_tiers_with_status(slug):
        rows.append({
            **t,
            "icon": TICKET_ICONS_BY_TIER.get((slug, t["idx"]), TICKET_ICONS.get(slug, "akoma")),
            "symbol_name": TIER_SYMBOL_NAMES.get((slug, t["idx"]), ""),
            "label": t.get("label") or labels.get(t["idx"], ""),
            "price": t["price_cents"] / 100,
        })
    return rows


def _bundle_card(bundle_slug, base_slug):
    """Card do pacote (ex: Full Pass + Caravan) -- preço, nome do símbolo e
    legenda do lote seguem sempre o lote ATUAL do componente com lotes
    (base_slug), por isso não precisam de lista de lotes própria: quando o
    Full Pass muda de lote, este card muda sozinho (ver
    Product.effective_price_cents)."""
    product = Product.query.filter_by(slug=bundle_slug, is_active=True).first()
    if not product:
        return None
    tier_idx = batch_tier_index(base_slug)
    base_symbol = TIER_SYMBOL_NAMES.get((base_slug, tier_idx), "") if tier_idx else ""
    tier_label = _tier_batch_labels().get(tier_idx, "") if tier_idx else ""
    return {
        "product": product,
        "icon": _ticket_icon(bundle_slug),
        "symbol_name": f"{base_symbol} & Fofo" if base_symbol else "",
        "tier_label": tier_label,
    }


# Nome do símbolo dos bilhetes de preço fixo (sem lotes) -- nome próprio,
# igual nas 3 línguas.
SIMPLE_SYMBOL_NAMES = {
    "passe-caravan": "Fofo",
    "bilhete-familia": "Sankofa",
    "bilhete-acessibilidade": "Base Saka",
    "bilhete-fim-de-semana": "Bi Nka Bi",
}


def _simple_card(slug):
    """Card de preço fixo (sem lotes) -- Caravan Pass, Family, Acessibilidade,
    Fim de Semana."""
    product = Product.query.filter_by(slug=slug, is_active=True).first()
    if not product:
        return None
    return {
        "product": product,
        "icon": TICKET_ICONS.get(slug, "akoma"),
        "symbol_name": SIMPLE_SYMBOL_NAMES.get(slug, ""),
    }


# ---------------------------------------------------------------------------
# Paginas de listagem
# ---------------------------------------------------------------------------

@shop_bp.route("/tickets")
def tickets():
    full_pass = Product.query.filter_by(slug="bilhete-geral", is_active=True).first()
    full_pass_tiers = _tier_rows("bilhete-geral") if full_pass else []

    return render_template(
        "tickets.html",
        full_pass=full_pass,
        full_pass_tiers=full_pass_tiers,
        caravan=_simple_card("passe-caravan"),
        bundle=_bundle_card("full-pass-caravan", "bilhete-geral"),
        family=_simple_card("bilhete-familia"),
        accessibility=_simple_card("bilhete-acessibilidade"),
        weekend=_simple_card("bilhete-fim-de-semana"),
    )


@shop_bp.route("/glamping-village")
def glamping_village_redirect():
    # Nome antigo da rota (antes de passar a "/the-experience", para bater
    # certo com a etiqueta "The Experience" no menu, ao estilo de
    # /the-festival e /the-map -- pedido do Joao). Mantemos isto a
    # redirecionar (em vez de apagar) para nao partir nenhum link que ja
    # tenha sido partilhado com este URL antigo (ex: testes, redes sociais).
    return redirect(url_for("shop.glamping"), code=301)


@shop_bp.route("/the-experience")
def glamping():
    products = Product.query.filter_by(category="glamping", is_active=True).order_by(Product.sort_order).all()
    return render_template("glamping.html", products=products)


@shop_bp.route("/merchandise")
def merchandise():
    products = Product.query.filter_by(category="merch", is_active=True).order_by(Product.sort_order).all()
    return render_template("merchandise.html", products=products)


@shop_bp.route("/product/<slug>")
def product_detail(slug):
    product = Product.query.filter_by(slug=slug, is_active=True).first_or_404()
    return render_template("product_detail.html", product=product)


# ---------------------------------------------------------------------------
# Carrinho (guardado na sessao)
# ---------------------------------------------------------------------------

def _cart():
    return session.setdefault("cart", {})


MAX_CART_QUANTITY = 20


def _validate_cart_line(product, variant, quantity):
    if not product or not product.is_active:
        abort(400, description="Produto indisponível.")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= MAX_CART_QUANTITY:
        abort(400, description="Quantidade inválida (máximo 20 unidades).")
    if (product.variant_list and variant not in product.variant_list) or (not product.variant_list and variant):
        abort(400, description="Variante inválida.")
    from ticket_sales import sale_available, remaining_capacity
    if product.category == "ticket" and not sale_available(product.slug):
        abort(409, description="Este bilhete não está à venda neste período.")
    for slug in [product.slug] + BUNDLES.get(product.slug, []):
        capacity = remaining_capacity(slug)
        if capacity is not None and quantity > capacity:
            abort(409, description="Não há bilhetes suficientes disponíveis.")
    components = BUNDLES.get(product.slug, [product.slug])
    from batches import current_tier
    for slug in components:
        if slug in TICKET_BATCHES:
            tier = current_tier(slug)
            if tier is None or (tier[2] is not None and quantity > tier[2]):
                abort(409, description="O lote não tem bilhetes suficientes.")


def _validate_cart_totals(lines):
    if sum(line["quantity"] for line in lines) > MAX_CART_QUANTITY:
        abort(400, description="Máximo de 20 unidades por encomenda.")
    from batches import current_tier
    counts = {}
    for line in lines:
        for slug in [line["product"].slug] + BUNDLES.get(line["product"].slug, []):
            counts[slug] = counts.get(slug, 0) + line["quantity"]
    from ticket_sales import remaining_capacity
    for slug, quantity in counts.items():
        capacity = remaining_capacity(slug)
        if capacity is not None and quantity > capacity:
            abort(409, description="O conjunto do carrinho excede os lugares disponíveis.")
        if slug in TICKET_BATCHES:
            tier = current_tier(slug)
            if tier is None or (tier[2] is not None and quantity > tier[2]):
                abort(409, description="O lote não tem bilhetes suficientes.")


def _cart_lines():
    """Devolve a lista de linhas do carrinho ja com o produto carregado da BD."""
    lines = []
    cart = _cart()
    stale_keys = []
    for key, qty in cart.items():
        try:
            product_id = int(key.split(":")[0])
        except (ValueError, TypeError, AttributeError):
            stale_keys.append(key)
            continue
        variant = key.split(":", 1)[1] if ":" in key else ""
        product = Product.query.get(product_id)
        if not product or not product.is_active or not isinstance(qty, int) or not 1 <= qty <= MAX_CART_QUANTITY:
            stale_keys.append(key)
            continue
        lines.append({
            "key": key,
            "product": product,
            "variant": variant,
            "quantity": qty,
            "subtotal": product.price * qty,
            # Bilhetes mostram o mesmo símbolo Adinkra usado na página de
            # bilhetes (ver TICKET_ICONS/_ticket_icon acima); os restantes
            # produtos (glamping/merch) mantêm o swatch de cor simples, que
            # não tem um símbolo equivalente.
            "icon": _ticket_icon(product.slug) if product.category == "ticket" else None,
        })
    for key in stale_keys:
        cart.pop(key, None)
    return lines


@shop_bp.app_context_processor
def inject_cart():
    lines = _cart_lines()
    count = sum(line["quantity"] for line in lines)
    total = sum(line["subtotal"] for line in lines)
    return {"cart_lines": lines, "cart_count": count, "cart_total": total}


@shop_bp.route("/cart")
def view_cart():
    return render_template("cart.html")


@shop_bp.route("/cart/mini-fragment")
def cart_mini_fragment():
    """Usado via AJAX para atualizar o conteúdo do drawer do carrinho sem recarregar a página."""
    return render_template("partials/_cart_lines.html")


@shop_bp.route("/cart/add", methods=["POST"])
def cart_add():
    product_id = request.form.get("product_id", type=int)
    variant = request.form.get("variant", "")
    quantity = request.form.get("quantity", default=1, type=int)

    product = Product.query.get_or_404(product_id)
    key = f"{product.id}:{variant}" if variant else str(product.id)

    cart = _cart()
    combined = cart.get(key, 0) + quantity if quantity is not None else None
    _validate_cart_line(product, variant, combined)
    if sum(cart.values()) - cart.get(key, 0) + combined > MAX_CART_QUANTITY:
        abort(400, description="Máximo de 20 unidades por encomenda.")
    cart[key] = combined
    session.modified = True

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        lines = _cart_lines()
        return jsonify({
            "ok": True,
            "cart_count": sum(l["quantity"] for l in lines),
            "cart_total": round(sum(l["subtotal"] for l in lines), 2),
            "added": product.localized_name,
        })

    flash(_('"%(name)s" adicionado ao carrinho.', name=product.localized_name), "success")
    return redirect(url_for("shop.view_cart"))


@shop_bp.route("/cart/update", methods=["POST"])
def cart_update():
    key = request.form.get("key")
    quantity = request.form.get("quantity", type=int)
    cart = _cart()
    if key in cart:
        if quantity and quantity > 0:
            product_id, _, variant = key.partition(":")
            _validate_cart_line(db.session.get(Product, int(product_id)), variant, quantity)
            if sum(cart.values()) - cart[key] + quantity > MAX_CART_QUANTITY:
                abort(400)
            cart[key] = quantity
        else:
            cart.pop(key, None)
        session.modified = True
    return redirect(url_for("shop.view_cart"))


@shop_bp.route("/cart/remove/<path:key>", methods=["POST"])
def cart_remove(key):
    cart = _cart()
    cart.pop(key, None)
    session.modified = True

    # Chamado via AJAX a partir do drawer lateral (ver initCart em main.js)
    # -- devolve JSON em vez de redirecionar, para o utilizador não perder
    # a página onde está (ex: /tickets) só por remover uma linha do
    # carrinho. O botão "Remover" da página /cart não manda este cabeçalho,
    # por isso continua a fazer o redirect normal, como sempre fez.
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        lines = _cart_lines()
        return jsonify({
            "ok": True,
            "cart_count": sum(l["quantity"] for l in lines),
            "cart_total": round(sum(l["subtotal"] for l in lines), 2),
        })

    return redirect(url_for("shop.view_cart"))


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


# Slugs dos bilhetes especiais que pedem um campo extra no checkout (ver
# _special_field_for_line/checkout, abaixo) -- companion_name para a
# Acessibilidade (acompanhante grátis), kids_names para a Família (menores
# de 18 anos grátis).
COMPANION_NAME_SLUG = "bilhete-acessibilidade"
KIDS_NAMES_SLUG = "bilhete-familia"


def _needs_ticket_names(product):
    """True para qualquer bilhete "normal" (Geral, Fim de Semana, Caravana,
    Full Pass + Caravan, cortesias, etc.) -- pedido explícito do João: se a
    linha tem quantidade > 1 (ou mesmo 1), cada bilhete gerado tem de poder
    ter o seu próprio nome, em vez de saírem todos com o nome de quem
    comprou. Acessibilidade e Família ficam de fora -- continuam a usar os
    seus próprios campos (companion_name/kids_names, acima), que não
    correspondem um-a-um com a quantidade (o acompanhante não conta como
    bilhete, e o número de crianças não tem de bater certo com a
    quantidade de adultos)."""
    return product.category == "ticket" and product.slug not in (COMPANION_NAME_SLUG, KIDS_NAMES_SLUG)


def _ticket_name_field(line, index):
    """Nome do <input> do nome do bilhete número `index` (0-based) desta
    linha do carrinho (ver templates/checkout.html) -- único mesmo com
    várias linhas/unidades."""
    return f"ticket_name__{line['key']}__{index}"


def _collect_ticket_names(lines):
    """Vai buscar ao formulário submetido os nomes de cada bilhete das
    linhas "normais" (ver _needs_ticket_names) -- devolve um dict
    {line_key: [nome_1, nome_2, ...]}, um nome por unidade de quantidade
    comprada nessa linha. Reaproveitado tanto para voltar a preencher o
    formulário quando há um erro de validação, como para gravar no
    OrderItem quando a encomenda é criada."""
    values = {}
    for line in lines:
        if not _needs_ticket_names(line["product"]):
            continue
        values[line["key"]] = [
            request.form.get(_ticket_name_field(line, i), "").strip()[:120]
            for i in range(line["quantity"])
        ]
    return values


def _special_field_name(line):
    """Nome do <input> extra desta linha do carrinho no formulário de
    checkout (ver templates/checkout.html) -- None se este produto não
    tiver nenhum campo especial. Usa-se line["key"] (a própria chave do
    carrinho) para o nome do campo ser único mesmo com várias linhas."""
    slug = line["product"].slug
    if slug == COMPANION_NAME_SLUG:
        return f"companion_name__{line['key']}"
    if slug == KIDS_NAMES_SLUG:
        return f"kids_names__{line['key']}"
    return None


def _collect_special_fields(lines):
    """Vai buscar ao formulário submetido o valor do campo especial de cada
    linha (ver _special_field_name) -- devolve um dict {line_key: valor},
    só com as linhas que têm mesmo um campo (as outras nem aparecem).
    Reaproveitado tanto para voltar a preencher o formulário quando há um
    erro de validação, como para gravar nos OrderItem quando a encomenda é
    criada."""
    values = {}
    for line in lines:
        field_name = _special_field_name(line)
        if field_name:
            values[line["key"]] = request.form.get(field_name, "").strip()[:300]
    return values


@shop_bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    lines = _cart_lines()
    if not lines:
        flash(_("O teu carrinho está vazio."), "error")
        return redirect(url_for("shop.view_cart"))

    if request.method == "POST":
        # Lock every catalog row in a stable order, including bundle components.
        # At this small catalog size this avoids a bundle/component lock inversion.
        Product.query.order_by(Product.id).with_for_update().populate_existing().all()
        for line in lines:
            _validate_cart_line(line["product"], line["variant"], line["quantity"])
        _validate_cart_totals(lines)
        name = request.form.get("name", "").strip()[:120]
        email = request.form.get("email", "").strip()
        country = request.form.get("country", "").strip()
        # Espaços/pontos são comuns quando as pessoas escrevem o NIF (ex:
        # "123 456 789") -- tiramos tudo o que não for dígito antes de
        # validar, para isso não ser motivo de erro.
        nif = re.sub(r"\D", "", request.form.get("nif", ""))
        special_fields = _collect_special_fields(lines)
        ticket_names = _collect_ticket_names(lines)

        errors = []
        if not name:
            errors.append(_("Indica o teu nome."))
        if not email or not EMAIL_RE.match(email):
            errors.append(_("Indica um email válido."))
        if not country:
            errors.append(_("Indica o teu país de origem."))
        if nif and not NIF_RE.match(nif):
            errors.append(_("O NIF deve ter 9 dígitos (ou deixa em branco se não precisares de fatura com NIF)."))
        # Um nome obrigatório por bilhete, para cada linha "normal" (ver
        # _needs_ticket_names) -- pedido explícito do João. Um único erro
        # por linha (em vez de um por bilhete em falta) para a mensagem não
        # ficar gigante numa encomenda com muitos bilhetes.
        for line in lines:
            if line["key"] not in ticket_names:
                continue
            if any(not n for n in ticket_names[line["key"]]):
                errors.append(_(
                    "Indica o nome de cada bilhete de %(product)s.",
                    product=line["product"].localized_name,
                ))

        if errors:
            for message in errors:
                flash(message, "error")
            return render_template(
                "checkout.html", lines=lines, countries=COUNTRIES,
                form_name=name, form_email=email, form_country=country, form_nif=nif,
                form_special_fields=special_fields, form_ticket_names=ticket_names,
                processing_fee=cart_processing_fee_cents(lines) / 100,
            )

        order = Order(
            name=name,
            email=email,
            country=country,
            nif=nif,
            lang_code=current_lang(),
            status="pending",
            reservation_expires_at=datetime.utcnow() + timedelta(minutes=35),
            total_cents=round(sum(l["subtotal"] for l in lines) * 100),
        )
        for line in lines:
            product = line["product"]
            order.items.append(OrderItem(
                product_id=product.id,
                product_name=product.localized_name,
                variant=line["variant"],
                quantity=line["quantity"],
                # Preço "a sério" no momento do checkout -- tem em conta
                # lotes/pacotes (ver batches.py). Gravamos também o lote,
                # para os bilhetes gerados depois usarem sempre o
                # template/preço que foi mesmo cobrado aqui, mesmo que o
                # lote mude entretanto.
                price_cents=product.effective_price_cents,
                batch_tier=resolve_batch_tier_for_order_item(product.slug),
                companion_name=special_fields.get(line["key"], "") if product.slug == COMPANION_NAME_SLUG else "",
                kids_names=special_fields.get(line["key"], "") if product.slug == KIDS_NAMES_SLUG else "",
                ticket_names=encode_ticket_names(ticket_names.get(line["key"], [])),
            ))
        # Recalcula o total a partir do preço efetivo de cada linha (em vez
        # de usar `lines`, que pode ter sido montado com o preço mostrado
        # uns segundos antes) -- assim o total cobrado bate sempre certo
        # com a soma das linhas gravadas. Depois soma-se a taxa de
        # processamento (ver fees.py) -- o total gravado fica já a ser o
        # valor a sério cobrado no Stripe (produtos + taxa).
        order.total_cents = sum(i.price_cents * i.quantity for i in order.items)
        order.total_cents += order_processing_fee_cents(order.items)
        db.session.add(order)
        db.session.commit()

        if current_app.config["PAYMENTS_LIVE"]:
            return _start_stripe_checkout(order)

        # --- Modo demo: sem Stripe configurado, marca como paga na hora ---
        order.status = "demo-paid"
        _finalize_paid_order(order)
        session["cart"] = {}
        return redirect(_success_url(order))

    return render_template(
        "checkout.html", lines=lines, countries=COUNTRIES,
        processing_fee=cart_processing_fee_cents(lines) / 100,
    )


def _extract_stripe_custom_field(stripe_session, key):
    """Vai buscar o valor de um custom_field (ver custom_fields em
    _start_stripe_checkout) a uma Session do Stripe -- funciona tanto com o
    objeto "a sério" devolvido por stripe.checkout.Session.retrieve como
    com o dict que vem dentro do payload do webhook, porque os dois
    suportam .get(). Devolve None se o cliente deixou o campo em branco
    (é opcional) ou se a sessão nem tinha este campo."""
    for field in (stripe_session.get("custom_fields") or []):
        if field.get("key") == key:
            numeric = field.get("numeric") or {}
            return numeric.get("value") or None
    return None


def _start_stripe_checkout(order):
    import stripe
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    # Usa sempre os OrderItem já gravados (não o carrinho) -- assim o que o
    # Stripe cobra bate SEMPRE certo com o que ficou na base de dados,
    # mesmo que o preço de algum lote tenha mudado nos segundos entre
    # montar o carrinho e clicar em pagar.
    line_items = []
    for item in order.items:
        display_name = item.product_name + (f" ({item.variant})" if item.variant else "")
        line_items.append({
            "price_data": {
                "currency": "eur",
                "product_data": {"name": display_name},
                "unit_amount": item.price_cents,
            },
            "quantity": item.quantity,
        })

    # Taxa de processamento (ver fees.py) -- linha à parte, para o cliente
    # ver claramente no checkout/recibo da Stripe o que é o bilhete e o
    # que é a comissão. Já está incluída em order.total_cents (ver
    # checkout() acima), por isso soma-se aqui aos line_items em vez de
    # ser recalculada -- os dois têm de bater sempre certo.
    fee_cents = order_processing_fee_cents(order.items)
    if fee_cents:
        line_items.append({
            "price_data": {
                "currency": "eur",
                "product_data": {"name": _("Taxa de processamento")},
                "unit_amount": fee_cents,
            },
            "quantity": 1,
        })

    checkout_session = stripe.checkout.Session.create(
        mode="payment",
        expires_at=int(order.reservation_expires_at.replace(tzinfo=timezone.utc).timestamp()),
        line_items=line_items,
        customer_email=order.email or None,
        success_url=current_app.config["DOMAIN"] + _success_url(order),
        client_reference_id=str(order.id),
        metadata={"order_id": str(order.id)},
        payment_intent_data={"metadata": {"order_id": str(order.id)}},
        idempotency_key=f"checkout-order-{order.id}",
        cancel_url=current_app.config["DOMAIN"] + url_for("shop.checkout_cancel"),
        # Pergunta o NIF diretamente na página do Stripe (opcional) -- fica
        # associado ao pagamento a sério (visível no dashboard do Stripe),
        # em vez de só existir no nosso lado. Se o cliente já tinha
        # preenchido o campo NIF no nosso checkout, este aqui vem
        # pré-preenchido só se o próprio Stripe achar por bem (não
        # controlamos isso); o valor que o cliente confirmar aqui é o que
        # fica a valer -- ver _extract_stripe_custom_field em
        # checkout_success/stripe_webhook, que substitui order.nif por
        # este assim que o pagamento é confirmado.
        custom_fields=[{
            "key": "nif",
            "label": {"type": "custom", "custom": _("NIF (opcional)")},
            "type": "numeric",
            "numeric": {"minimum_length": 9, "maximum_length": 9},
            "optional": True,
        }],
    )
    order.stripe_session_id = checkout_session.id
    db.session.commit()
    return redirect(checkout_session.url, code=303)


@shop_bp.route("/checkout/success/<int:order_id>")
def checkout_success(order_id):
    if not request.args.get("token"):
        # Sessions opened before the migration have an unsigned return URL.
        # Keep a useful receipt page without revealing any order-specific data.
        return render_template("checkout_received.html")
    try:
        token_order = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="order-confirmation").loads(
            request.args.get("token", ""), max_age=7 * 24 * 3600)
        if token_order != order_id:
            abort(404)
    except (BadSignature, SignatureExpired):
        abort(404)
    order = Order.query.get_or_404(order_id)
    if current_app.config["PAYMENTS_LIVE"] and order.status in ("pending", "processing", "paid") and order.stripe_session_id:
        import stripe
        stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
        try:
            stripe_session = stripe.checkout.Session.retrieve(order.stripe_session_id)
            _confirm_stripe_payment(stripe_session)
        except Exception:
            db.session.rollback()
            current_app.logger.warning("Payment confirmation deferred to webhook for order %s", order.id)
    if order.status in ("paid", "demo-paid"):
        session["cart"] = {}
    return render_template("checkout_success.html", order=order)


@shop_bp.route("/checkout/cancel")
def checkout_cancel():
    return render_template("checkout_cancel.html")


@shop_bp.route("/webhooks/stripe", methods=["POST"])
def stripe_webhook():
    """Chamado diretamente pelo Stripe quando algo acontece a um pagamento.

    E isto que torna a confirmacao de encomendas fiavel: mesmo que o
    cliente feche o telemovel ou perca a ligacao antes de voltar ao site,
    o Stripe chama este endpoint na mesma e a encomenda fica marcada como
    paga. Sem isto dependiamos só do browser do cliente voltar a pagina de
    sucesso, o que nem sempre acontece.
    """
    import stripe
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    webhook_secret = current_app.config.get("STRIPE_WEBHOOK_SECRET", "")

    if not webhook_secret:
        return "", 503
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        return "", 400
    try:
        event_type = event.get("type")
        obj = event.get("data", {}).get("object", {})
        from transfers import handle_transfer_event
        if event_type.startswith('checkout.session.') and handle_transfer_event(event_type,obj):
            return '',200
        if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            _confirm_stripe_payment(obj)
        elif event_type == "charge.refunded" and obj.get("refunded") is True:
            # Resolve via Stripe, rather than trusting a browser-supplied order ID.
            stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
            sessions = stripe.checkout.Session.list(payment_intent=obj.get("payment_intent"), limit=10)
            for payment_session in sessions.get("data", []):
                order = Order.query.filter_by(stripe_session_id=payment_session.get("id")).with_for_update().first()
                if order and order.status == "paid":
                    order.status = "refunded"
            db.session.commit()
        elif event_type in ("checkout.session.expired", "checkout.session.async_payment_failed"):
            order = Order.query.filter_by(stripe_session_id=obj.get("id", "")).with_for_update().first()
            if order and order.status in ("pending", "processing"):
                order.status = "cancelled"
                db.session.commit()
    except ValueError:
        db.session.rollback()
        current_app.logger.warning("Stripe event rejected: payment/order mismatch")
        return "", 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Stripe fulfillment failed; webhook retry required")
        return "", 500
    return "", 200
