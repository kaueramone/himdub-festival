"""Modelos da base de dados (SQLAlchemy).

Sao 3 tabelas simples:
- Product   -> tudo o que se pode comprar (bilhetes, glamping, merchandise)
- Order     -> uma encomenda feita por um visitante
- OrderItem -> cada linha de uma encomenda (produto + variante + quantidade)
"""

import json

from datetime import datetime
from extensions import db
from i18n import current_lang, DEFAULT_LANGUAGE


def _format_kids_names(raw):
    """'Chloé Bernal, João Cardoso' -> 'Kids 1: Chloé Bernal, Kids 2: João
    Cardoso' -- formato pedido pelo João para aparecer no check-in e no
    export Excel (ver OrderItem.formatted_kids_names/Ticket.formatted_kids_names,
    abaixo). Guarda-se sempre o texto tal como o cliente escreveu no
    checkout (campo kids_names) -- isto é só para apresentação."""
    if not raw:
        return ""
    names = [n.strip() for n in raw.split(",") if n.strip()]
    return ", ".join(f"Kids {i}: {name}" for i, name in enumerate(names, start=1))


def encode_ticket_names(names):
    """Guarda a lista de nomes (um por unidade comprada nessa linha, ver
    OrderItem.ticket_names abaixo) como JSON num único campo de texto --
    mais seguro do que juntar por vírgulas porque um nome pode ter vírgula
    lá dentro. `names` pode vir vazia (linha que não é deste tipo de
    bilhete)."""
    return json.dumps(names) if names else ""


def decode_ticket_names(raw):
    """Inverso de encode_ticket_names -- devolve sempre uma lista (vazia se
    não houver nada gravado ou se o JSON estiver corrompido, para nunca
    rebentar a geração de bilhetes por causa disto)."""
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


class Product(db.Model):
    """`name`/`short_description`/`description` guardam sempre o texto em
    PORTUGUÊS (o idioma "de origem"/por defeito do site). As colunas
    `*_en`/`*_fr` guardam as traduções -- se estiverem vazias, as
    propriedades `localized_*` voltam a mostrar o texto em português em vez
    de ficarem em branco (ver `_localized`)."""

    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(120), unique=True, nullable=False)
    category = db.Column(db.String(30), nullable=False)  # ticket | glamping | merch
    name = db.Column(db.String(120), nullable=False)
    name_en = db.Column(db.String(120), default="")
    name_fr = db.Column(db.String(120), default="")
    short_description = db.Column(db.String(255), default="")
    short_description_en = db.Column(db.String(255), default="")
    short_description_fr = db.Column(db.String(255), default="")
    description = db.Column(db.Text, default="")
    description_en = db.Column(db.Text, default="")
    description_fr = db.Column(db.Text, default="")
    price_cents = db.Column(db.Integer, nullable=False)
    image_class = db.Column(db.String(60), default="ph-forest")  # classe CSS do placeholder visual
    variants = db.Column(db.String(255), default="")  # ex: "S,M,L,XL" - vazio = sem variantes
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)

    @property
    def effective_price_cents(self):
        """Preço atual em cêntimos -- igual a `price_cents` para produtos
        normais, mas para bilhetes com lotes (ver batches.py) ou pacotes
        (ex: Full Pass + Caravan) calcula o preço de acordo com quantos já
        foram vendidos. É isto que deve ser usado sempre que se cobra a
        sério (checkout, Stripe) -- `price_cents` fica só como valor de
        referência/fallback."""
        from batches import effective_price_cents as _effective
        cents = _effective(self.slug)
        return cents if cents is not None else self.price_cents

    @property
    def price(self):
        return self.effective_price_cents / 100

    @property
    def variant_list(self):
        return [v.strip() for v in self.variants.split(",") if v.strip()]

    def _localized(self, field):
        lang = current_lang()
        if lang != DEFAULT_LANGUAGE:
            translated = getattr(self, f"{field}_{lang}", "") or ""
            if translated.strip():
                return translated
        return getattr(self, field) or ""

    def name_in(self, lang_code):
        """Como `_localized("name")`, mas para um idioma explícito em vez do
        idioma do pedido HTTP atual -- necessário para gerar bilhetes fora
        de um pedido (ex: a partir do webhook do Stripe), onde não há
        sessão/idioma "atual" fiável (usa-se antes o idioma guardado na
        própria encomenda, `order.lang_code`)."""
        if lang_code and lang_code != DEFAULT_LANGUAGE:
            translated = getattr(self, f"name_{lang_code}", "") or ""
            if translated.strip():
                return translated
        return self.name or ""

    @property
    def localized_name(self):
        return self._localized("name")

    @property
    def localized_short_description(self):
        return self._localized("short_description")

    @property
    def localized_description(self):
        return self._localized("description")

    def __repr__(self):
        return f"<Product {self.slug}>"


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    name = db.Column(db.String(120), default="")
    email = db.Column(db.String(120), default="")
    # País de origem do cliente -- só para a nossa base de dados/estatísticas
    # internas, nunca aparece nos bilhetes nem no email de confirmação.
    country = db.Column(db.String(80), default="")
    # NIF (opcional) -- só para o cliente pedir fatura com número de
    # contribuinte para a contabilidade. Não aparece nos bilhetes nem no
    # email de confirmação, só na exportação Excel (ver blueprints/admin.py).
    nif = db.Column(db.String(20), default="")
    # Idioma em que o cliente fez o checkout (pt/en/fr) -- guardado para o
    # email de confirmação com os bilhetes sair no mesmo idioma.
    lang_code = db.Column(db.String(5), default=DEFAULT_LANGUAGE)
    status = db.Column(db.String(30), default="pending")  # pending | paid | demo-paid | comp | cancelled
    total_cents = db.Column(db.Integer, default=0)
    stripe_session_id = db.Column(db.String(255), default="")
    reservation_expires_at = db.Column(db.DateTime, nullable=True, index=True)
    # Fica True assim que o email com os bilhetes/QR e enviado com sucesso,
    # para nunca reenviarmos o mesmo email duas vezes (ver _finalize_paid_order).
    email_sent = db.Column(db.Boolean, default=False)

    items = db.relationship("OrderItem", backref="order", cascade="all, delete-orphan", order_by="OrderItem.id")

    @property
    def total(self):
        return self.total_cents / 100


class AdminUser(db.Model):
    __tablename__ = "admin_users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(180), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    session_version = db.Column(db.Integer, default=1, nullable=False)
    totp_secret = db.Column(db.String(64), default="")
    totp_last_counter = db.Column(db.Integer, default=-1, nullable=False)


class LoginThrottle(db.Model):
    __tablename__ = "login_throttles"
    key = db.Column(db.String(64), primary_key=True)
    window_start = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)


class AdminInvite(db.Model):
    __tablename__ = "admin_invites"
    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("admin_users.id"), nullable=False)
    email = db.Column(db.String(180), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    revoked = db.Column(db.Boolean, default=False, nullable=False)


class AdminEnrollment(db.Model):
    __tablename__ = "admin_enrollments"
    id = db.Column(db.String(64), primary_key=True)
    invite_id = db.Column(db.Integer, db.ForeignKey("admin_invites.id"), nullable=False, index=True)
    email = db.Column(db.String(180), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    totp_secret = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)


class PageAppearance(db.Model):
    __tablename__ = "page_appearances"
    key = db.Column(db.String(40), primary_key=True)
    mode = db.Column(db.String(16), default="original", nullable=False)
    image_url = db.Column(db.String(500), default="")
    color_start = db.Column(db.String(7), default="#183629")
    color_end = db.Column(db.String(7), default="#071d16")
    layout = db.Column(db.String(16), default="cards")


class PageBlock(db.Model):
    __tablename__ = "page_blocks"
    id = db.Column(db.Integer, primary_key=True)
    page_key = db.Column(db.String(40), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    text = db.Column(db.Text, default="")
    image_url = db.Column(db.String(500), default="")
    link_url = db.Column(db.String(500), default="")
    link_label = db.Column(db.String(80), default="Saber mais")
    title_en = db.Column(db.String(180), default="")
    title_fr = db.Column(db.String(180), default="")
    text_en = db.Column(db.Text, default="")
    text_fr = db.Column(db.Text, default="")
    sort_order = db.Column(db.Integer, default=0)
    published = db.Column(db.Boolean, default=False, nullable=False)


class NativeContent(db.Model):
    """Overrides and copies of the original editorial items; no executable markup."""
    __tablename__ = "native_contents"
    key = db.Column(db.String(100), primary_key=True)
    page_key = db.Column(db.String(40), nullable=False, index=True)
    payload = db.Column(db.Text, nullable=False)


class GalleryAlbum(db.Model):
    __tablename__ = "gallery_albums"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, default="")
    cover_url = db.Column(db.String(500), default="")
    published = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    photos = db.relationship("GalleryPhoto", backref="album", cascade="all, delete-orphan", order_by="GalleryPhoto.sort_order, GalleryPhoto.id")


class GalleryPhoto(db.Model):
    __tablename__ = "gallery_photos"
    id = db.Column(db.Integer, primary_key=True)
    album_id = db.Column(db.Integer, db.ForeignKey("gallery_albums.id"), nullable=False, index=True)
    image_url = db.Column(db.String(500), nullable=False)
    caption = db.Column(db.String(255), default="")
    sort_order = db.Column(db.Integer, default=0)


class NewsletterSubscriber(db.Model):
    __tablename__ = "newsletter_subscribers"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(180), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    requested_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    confirmed_at = db.Column(db.DateTime)
    unsubscribed_at = db.Column(db.DateTime)
    nonce = db.Column(db.String(64), nullable=False)
    consent_text = db.Column(db.String(500), nullable=False)
    provider_contact_id = db.Column(db.String(100), default="")
    synced_at = db.Column(db.DateTime)


class WebsiteJob(db.Model):
    __tablename__ = "website_jobs"
    id = db.Column(db.String(64), primary_key=True)
    kind = db.Column(db.String(40), nullable=False)
    payload = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    available_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    first_attempt_at = db.Column(db.DateTime)
    lease_until = db.Column(db.DateTime)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    last_error = db.Column(db.String(100), default="")


class TicketTransfer(db.Model):
    __tablename__ = "ticket_transfers"
    id = db.Column(db.String(40), primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    active_ticket_id = db.Column(db.Integer, unique=True, nullable=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    original_code_hash = db.Column(db.String(64), nullable=False)
    original_email = db.Column(db.String(180), nullable=False)
    new_name = db.Column(db.String(120), default="")
    new_email = db.Column(db.String(180), default="")
    fee_cents = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(30), default="awaiting_email", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    checkout_expires_at = db.Column(db.DateTime)
    stripe_session_id = db.Column(db.String(255), unique=True, nullable=True)
    payment_intent = db.Column(db.String(255), default="")
    completed_at = db.Column(db.DateTime)


class AuditEvent(db.Model):
    __tablename__ = "audit_events"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    actor = db.Column(db.String(180), nullable=False)
    action = db.Column(db.String(80), nullable=False)
    target = db.Column(db.String(80), default="")
    details = db.Column(db.Text, default="")


class EmailDelivery(db.Model):
    __tablename__ = "email_deliveries"
    id = db.Column(db.String(64), primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    recipient = db.Column(db.String(180), nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    available_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    lease_until = db.Column(db.DateTime)
    lease_token = db.Column(db.String(40), default="")
    first_attempt_at = db.Column(db.DateTime)
    payload_hash = db.Column(db.String(64))
    revision = db.Column(db.String(64), nullable=False)
    provider_message_id = db.Column(db.String(180), default="")
    last_error = db.Column(db.String(120), default="")


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"))
    product_name = db.Column(db.String(120))
    variant = db.Column(db.String(60), default="")
    quantity = db.Column(db.Integer, default=1)
    price_cents = db.Column(db.Integer, default=0)
    # Lote (ver batches.py) que estava ativo quando esta linha foi cobrada
    # -- ex: 1 = Early Bird, 2 = 2º lote. None = produto sem lotes. Gravado
    # aqui (e não recalculado depois) para os bilhetes gerados a partir
    # desta linha usarem sempre o template/preço que foi mesmo cobrado,
    # mesmo que entretanto o lote tenha mudado.
    batch_tier = db.Column(db.Integer, nullable=True)

    # Campos extra só preenchidos para bilhetes especiais (ver checkout()
    # em blueprints/shop.py, que só mostra estes campos no formulário
    # quando a linha é mesmo desse tipo de bilhete):
    #  - companion_name: nome do acompanhante, para o bilhete de
    #    Acessibilidade (o acompanhante entra grátis, mas fica registado
    #    quem é, para a organização/porteiro saberem).
    #  - kids_names: nomes das crianças, para o bilhete de Família (menores
    #    de 18 anos entram grátis) -- guardado tal como o cliente escreveu
    #    (ex: "Chloé Bernal, João Cardoso"); ver formatted_kids_names, mais
    #    abaixo, para o formato "Kids 1: ..., Kids 2: ..." mostrado no
    #    admin/check-in/export.
    companion_name = db.Column(db.String(120), default="")
    kids_names = db.Column(db.Text, default="")

    # Nome de cada bilhete individual desta linha, para linhas de bilhete
    # "normais" (tudo exceto Acessibilidade/Família, acima) com
    # quantidade > 1 -- pedido explícito do João: se alguém compra 5x
    # Bilhete Geral, cada um dos 5 tem de poder ter um nome diferente, em
    # vez de todos ficarem só com o nome de quem comprou. Guardado como
    # JSON (ver encode_ticket_names/decode_ticket_names, acima) com um nome
    # por unidade, na mesma ordem em que os Tickets são gerados (ver
    # ticketing.py generate_tickets_for_order, que copia cada nome para o
    # `holder_name` do Ticket correspondente).
    ticket_names = db.Column(db.Text, default="")

    # Mesma ideia que ticket_names, acima, mas para o email de cada bilhete
    # -- pedido explícito do João para poder reencaminhar um bilhete a
    # sério para o email da pessoa que o vai usar (em vez de ir sempre só
    # para o email de quem comprou). Ao contrário do nome, isto não se
    # preenche no checkout -- começa sempre vazio (todos os bilhetes vão
    # para order.email, como sempre foi) e só é preenchido mais tarde, à
    # mão, no admin (ver edit_order_name em blueprints/admin.py), quando
    # se souber o email a sério de cada pessoa. Ver send_ticket_email em
    # emailing.py para a lógica de agrupar/enviar por email diferente.
    ticket_emails = db.Column(db.Text, default="")

    @property
    def subtotal(self):
        return self.price_cents * self.quantity / 100

    @property
    def formatted_kids_names(self):
        return _format_kids_names(self.kids_names)

    @property
    def ticket_names_list(self):
        return decode_ticket_names(self.ticket_names)

    @property
    def ticket_emails_list(self):
        return decode_ticket_names(self.ticket_emails)


class Ticket(db.Model):
    """Um bilhete físico individual (um por unidade comprada de um produto
    da categoria "ticket" -- ex: uma encomenda de 2x "Bilhete Geral" gera 2
    Tickets separados, cada um com o seu próprio código/QR).

    O `code` é o que vai no QR code enviado por email. À entrada, o porteiro
    lê o QR (ver blueprints/checkin.py), o que identifica este bilhete, e
    associa-o ao número da pulseira cashless (GoFun) entregue ao cliente.
    """
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    order_item_id = db.Column(db.Integer, db.ForeignKey("order_items.id"), nullable=False)
    product_name = db.Column(db.String(120), default="")
    variant = db.Column(db.String(60), default="")
    # Slug do produto que este bilhete representa "a sério" -- normalmente
    # igual ao do OrderItem, mas para pacotes (ex: Full Pass + Caravan) uma
    # única linha de encomenda gera vários Tickets, um por componente, cada
    # um com o seu próprio product_slug (ex: "bilhete-geral" e
    # "passe-caravan"). É por isto (e não pelo OrderItem) que se sabe que
    # template/QR usar e se este bilhete conta para algum lote com limite
    # -- ver ticket_image.py e batches.py.
    product_slug = db.Column(db.String(120), default="")
    # Lote ativo quando este bilhete foi emitido (ver OrderItem.batch_tier).
    batch_tier = db.Column(db.Integer, nullable=True)

    # Copiados do OrderItem no momento em que o bilhete é gerado (ver
    # ticketing.py generate_tickets_for_order) -- só preenchidos para
    # bilhetes de Acessibilidade/Família (ver comentário em OrderItem,
    # acima). Ficam duplicados aqui (e não só no OrderItem) para o
    # check-in à entrada (templates/checkin/ticket.html) conseguir
    # mostrá-los sem precisar de ir buscar o OrderItem à parte.
    companion_name = db.Column(db.String(120), default="")
    kids_names = db.Column(db.Text, default="")

    # Nome de quem vai usar ESTE bilhete em concreto -- pedido pelo João
    # para compras de vários bilhetes iguais (ex: 5x Bilhete Geral) poderem
    # ter um nome diferente em cada um, em vez de saírem todos com o nome
    # de quem comprou. Só preenchido para bilhetes "normais" (ver
    # OrderItem.ticket_names, acima) -- fica vazio para Acessibilidade/
    # Família (que continuam a usar companion_name/kids_names) e para
    # bilhetes antigos gerados antes desta coluna existir; nesses casos o
    # bilhete continua a mostrar o nome do comprador (ver ticket_image.py).
    holder_name = db.Column(db.String(120), default="")

    # Email de quem vai usar ESTE bilhete em concreto -- só preenchido
    # quando um admin o corrige explicitamente (ver OrderItem.ticket_emails,
    # acima, e edit_order_name em blueprints/admin.py). Vazio = continua a
    # ir para order.email, como sempre foi -- este campo nunca é pedido no
    # checkout, só existe para corrigir/reencaminhar depois da compra.
    holder_email = db.Column(db.String(180), default="")

    wristband_code = db.Column(db.String(60), default="")
    checked_in_at = db.Column(db.DateTime)
    checked_in_by = db.Column(db.String(80), default="")

    # Historial de codigos antigos deste bilhete (separados por virgula),
    # preenchido quando um admin corrige o nome da encomenda e o codigo e
    # substituido -- ver edit_order_name em blueprints/admin.py. Serve so
    # de rasto para suporte (ex: "o meu bilhete tinha o codigo X"); o QR
    # antigo deixa de funcionar no check-in assim que o `code` muda.
    previous_codes = db.Column(db.Text, default="")

    order = db.relationship("Order", backref=db.backref("tickets", order_by="Ticket.id"))
    order_item = db.relationship("OrderItem")

    @property
    def is_checked_in(self):
        return self.checked_in_at is not None

    @property
    def formatted_kids_names(self):
        return _format_kids_names(self.kids_names)


class Setting(db.Model):
    """Tabela genérica chave/valor para opções que precisam de poder ser
    ligadas pelo João no admin, sem precisar de deploy nem de mexer em
    código -- por agora só uma: o momento em que a contagem de vendas "a
    sério" começa para o sistema de lotes (ver batches.py,
    real_sales_launch_at/activate_real_sales_launch).

    É uma tabela nova (não uma coluna nova numa tabela existente), por isso
    não precisa de nenhuma entrada em db_migrate.py -- o db.create_all() em
    app.py cria-a sozinha, automaticamente, no arranque a seguir ao
    deploy."""
    __tablename__ = "settings"

    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(255), default="")
