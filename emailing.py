"""Envio do email de confirmação com os bilhetes/QR code (via Resend).

Tal como o Stripe, isto tem um "modo demo": sem RESEND_API_KEY configurada
no .env, o email não é enviado a sério, mas os bilhetes continuam a ser
gerados na mesma (vê-los em /admin/orders). Assim dá para testar o fluxo
todo sem precisares logo de uma conta Resend.

O corpo do email é o template gráfico em templates/email/ticket_confirmation.html
(desenhado pelo João), renderizado com Jinja como qualquer outra página do
site -- ver build_ticket_email_html() mais abaixo.

Imagens do email (logo, fundo do bilhete, QR code): vão embutidas no
proprio email como anexos "inline" (técnica CID -- Content-ID), NÃO como
`data:image/...;base64,...` nem como URLs para o site. Duas razões:
  1. O Gmail (e vários outros clientes de email) bloqueia/ignora imagens
     em base64 dentro do HTML por defeito -- e era exatamente isso que
     fazia o QR code não aparecer no corpo do email (o PDF em anexo
     continuava bem, porque esse não depende do HTML do email).
  2. Usar um URL (ex: "{DOMAIN}/static/img/logo.png") só funciona se
     DOMAIN for um endereço a sério, acessível pela internet -- em
     desenvolvimento local (DOMAIN=http://127.0.0.1:...) isso nunca
     funciona, porque é um endereço que só existe dentro do teu proprio
     computador. CID evita este problema por completo, tanto em
     desenvolvimento como em produção.
"""

import base64
import logging
import os

import requests
from flask import current_app

logger = logging.getLogger(__name__)

from models import Product
from ticket_image import generate_ticket_image_bytes
from ticketing import ticket_qr_png_bytes
from fees import order_processing_fee_cents

# Textos fixos do email, no idioma em que o cliente fez o checkout
# (order.lang_code) -- guardado nesse idioma precisamente para isto. Não usa
# o Flask-Babel diretamente porque o email pode ser construído fora de um
# pedido HTTP normal (ex: a partir do webhook do Stripe, ou do script
# resend_ticket.py, que só tem app_context, sem request_context).
EMAIL_STRINGS = {
    "pt": {
        "subject": "Os teus bilhetes — HIM Dub Festival (encomenda #{id})",
        "email_title": "Os teus bilhetes — HIM Dub Festival",
        "preheader": "A tua encomenda está confirmada -- os bilhetes vão em anexo e aqui em baixo.",
        "greeting": "Obrigado, {name}!",
        "greeting_fallback": "festivaleiro",
        "confirmed": "A tua encomenda #{id} para o HIM Dub Festival está confirmada.",
        "attachment_hint": "O teu bilhete também vai em anexo, pronto a mostrar ou imprimir.",
        "see_you_soon": "Guarda este email -- vais precisar do QR code à entrada. Vemo-nos junto ao rio!",
        "ticket_label": "Bilhete",
        "qr_alt": "Código QR do bilhete",
        "code_label": "Código:",
        "qr_hint": "Mostra este QR à entrada -- é associado a uma pulseira cashless.",
        "personal_ticket_title": "Bilhete pessoal e intransmissível.",
        "personal_ticket_body": "Não partilhes nem publiques este código QR -- cada código só pode ser validado uma vez.",
        "summary": "Resumo da encomenda",
        "processing_fee": "Taxa de processamento",
        "total": "Total",
        "entry_info_title": "O que precisas de saber antes de chegar",
        "entry_info_lines": [
            "Traz contigo um documento de identificação válido.",
            "Portões abertos a partir das 12:00 no Dia de Boas-vindas (23 de agosto).",
            "É obrigatório trocar o teu bilhete por uma pulseira à entrada.",
            "Detalhes sobre o ponto de acesso e mapa do recinto seguem em breve.",
        ],
        "not_allowed_title": "Objetos e situações não permitidos",
        "not_allowed_lines": [
            "Fogueiras ou material pirotécnico.",
            "Garrafas ou botijas de gás.",
            "Geradores, fogareiros ou churrascos.",
            "Bebidas alcoólicas próprias dentro da zona do festival (só permitido na zona de campismo/caravanas).",
            "Latas e vidro.",
            "Cães -- não são permitidos.",
        ],
        "rules_link_label": "Regulamento completo:",
        "terms_link_text": "ver aqui",
        "keep_ticket_note": "Guarda este email até ao fim do festival -- pode ser-te pedido para confirmar a tua identidade à entrada.",
        "footer": "HIM Dub Festival — vemo-nos junto ao rio.",
        "contact_label": "Contacto",
        "terms_label": "Regras do festival",
    },
    "en": {
        "subject": "Your tickets — HIM Dub Festival (order #{id})",
        "email_title": "Your tickets — HIM Dub Festival",
        "preheader": "Your order is confirmed -- your tickets are attached and right here below.",
        "greeting": "Thank you, {name}!",
        "greeting_fallback": "festival-goer",
        "confirmed": "Your order #{id} for HIM Dub Festival is confirmed.",
        "attachment_hint": "Your ticket is also attached, ready to show or print.",
        "see_you_soon": "Keep this email -- you'll need the QR code at the entrance. See you by the river!",
        "ticket_label": "Ticket",
        "qr_alt": "Ticket QR code",
        "code_label": "Code:",
        "qr_hint": "Show this QR code at the entrance -- it will be linked to a cashless wristband.",
        "personal_ticket_title": "Personal, non-transferable ticket.",
        "personal_ticket_body": "Don't share or post this QR code -- each code can only be validated once.",
        "summary": "Order summary",
        "processing_fee": "Processing fee",
        "total": "Total",
        "entry_info_title": "What you need to know before you arrive",
        "entry_info_lines": [
            "Bring a valid ID document with you.",
            "Gates open from 12:00 on Welcome Day (August 23).",
            "You must exchange your ticket for a wristband at the entrance.",
            "Details about the access point and venue map coming soon.",
        ],
        "not_allowed_title": "Not allowed",
        "not_allowed_lines": [
            "Campfires or pyrotechnic materials.",
            "Gas bottles or canisters.",
            "Generators, camping stoves or barbecues.",
            "Your own alcoholic drinks inside the festival area (only allowed in the camping/caravan zone).",
            "Cans and glass.",
            "Dogs -- not allowed.",
        ],
        "rules_link_label": "Full rules:",
        "terms_link_text": "see here",
        "keep_ticket_note": "Keep this email until the end of the festival -- you may be asked to confirm your identity at the entrance.",
        "footer": "HIM Dub Festival — see you by the river.",
        "contact_label": "Contact",
        "terms_label": "Festival rules",
    },
    "fr": {
        "subject": "Tes billets — HIM Dub Festival (commande #{id})",
        "email_title": "Tes billets — HIM Dub Festival",
        "preheader": "Ta commande est confirmée -- tes billets sont en pièce jointe et juste ici.",
        "greeting": "Merci, {name} !",
        "greeting_fallback": "festivalier",
        "confirmed": "Ta commande #{id} pour le HIM Dub Festival est confirmée.",
        "attachment_hint": "Ton billet est aussi en pièce jointe, prêt à montrer ou à imprimer.",
        "see_you_soon": "Garde cet email -- tu auras besoin du QR code à l'entrée. On se retrouve au bord de la rivière !",
        "ticket_label": "Billet",
        "qr_alt": "QR code du billet",
        "code_label": "Code :",
        "qr_hint": "Montre ce QR code à l'entrée -- il sera associé à un bracelet cashless.",
        "personal_ticket_title": "Billet personnel et incessible.",
        "personal_ticket_body": "Ne partage ni ne publie ce QR code -- chaque code ne peut être validé qu'une seule fois.",
        "summary": "Résumé de la commande",
        "processing_fee": "Frais de traitement",
        "total": "Total",
        "entry_info_title": "Ce qu'il faut savoir avant d'arriver",
        "entry_info_lines": [
            "Munis-toi d'une pièce d'identité valide.",
            "Portes ouvertes à partir de 12h00 le jour d'accueil (23 août).",
            "L'échange de ton billet contre un bracelet à l'entrée est obligatoire.",
            "Les détails sur le point d'accès et le plan du site arrivent bientôt.",
        ],
        "not_allowed_title": "Non autorisé",
        "not_allowed_lines": [
            "Feux de camp ou matériel pyrotechnique.",
            "Bouteilles ou bonbonnes de gaz.",
            "Groupes électrogènes, réchauds ou barbecues.",
            "Boissons alcoolisées personnelles à l'intérieur de la zone du festival (autorisées uniquement dans la zone camping/caravanes).",
            "Canettes et verre.",
            "Chiens -- non autorisés.",
        ],
        "rules_link_label": "Règlement complet :",
        "terms_link_text": "voir ici",
        "keep_ticket_note": "Garde cet email jusqu'à la fin du festival -- on pourra te demander de confirmer ton identité à l'entrée.",
        "footer": "HIM Dub Festival — on se retrouve au bord de la rivière.",
        "contact_label": "Contact",
        "terms_label": "Règles du festival",
    },
}

# Datas/local do festival, já traduzidos (o texto "de origem" vive em
# config.FESTIVAL_DATES/FESTIVAL_LOCATION em português -- aqui está só a
# tradução manual dessas duas frases curtas para o email, tal como já
# existe para o resto do site em translations/{en,fr}).
FESTIVAL_DATES_BY_LANG = {
    "pt": "23 a 29 de agosto de 2027",
    "en": "August 23–29, 2027",
    "fr": "Du 23 au 29 août 2027",
}
FESTIVAL_VENUE_BY_LANG = {
    "pt": "Praia Fluvial de Rapoula do Côa, Sabugal (Guarda)",
    "en": "Rapoula do Côa River Beach, Sabugal (Guarda)",
    "fr": "Plage fluviale de Rapoula do Côa, Sabugal (Guarda)",
}


def _strings(order):
    return EMAIL_STRINGS.get(order.lang_code or "pt", EMAIL_STRINGS["pt"])


def _localized_path(path, lang_code):
    """Acrescenta o prefixo de idioma (/en/..., /fr/...) a um caminho do
    site, tal como localized_url_for faz nos templates -- mas sem precisar
    de request_context (isto pode correr fora de um pedido HTTP, ex: no
    webhook do Stripe ou no script resend_ticket.py)."""
    if lang_code and lang_code != "pt":
        return f"/{lang_code}{path}"
    return path


def _order_items_context(order):
    lines = []
    for item in order.items:
        # Mesma lógica dos bilhetes acima: item.product_name é sempre
        # português, por isso vai-se buscar o nome traduzido ao Product
        # (pelo product_id) quando ele ainda existir.
        product = Product.query.get(item.product_id) if item.product_id else None
        name = product.name_in(order.lang_code) if product else item.product_name
        lines.append({
            "quantity": item.quantity,
            "name": name,
            "variant": item.variant,
            "subtotal": item.subtotal,
        })
    return lines


def _ticket_qr_content_id(ticket):
    """Content-ID único do QR deste bilhete dentro do email (uma encomenda
    pode ter vários bilhetes, por isso não pode ser um id fixo)."""
    return f"ticket-qr-{ticket.code}"


def _tickets_context(tickets, lang_code):
    """Um dict por bilhete -- o QR é referenciado como "cid:..." (ver
    build_inline_image_attachments mais abaixo, que é quem de facto anexa
    a imagem ao email com esse Content-ID).

    Recebe a lista de `Ticket` a incluir (não a encomenda inteira) --
    normalmente `order.tickets`, mas quando a encomenda é enviada em vários
    emails separados (ver _group_tickets_by_recipient, mais abaixo) é só o
    subconjunto de bilhetes desse destinatário, para cada pessoa só ver o
    seu próprio QR, nunca o de outra."""
    result = []
    for ticket in tickets:
        # ticket.product_name fica gravado sempre em português (é uma cópia
        # do nome do produto feita no momento da compra); para o email sair
        # no idioma certo, vamos antes buscar o nome traduzido ao Product
        # pelo slug -- mesma ideia do name_in() já usado para os bilhetes
        # PDF. Se o produto tiver entretanto sido apagado, cai no nome
        # gravado no bilhete (nunca fica em branco).
        product = Product.query.filter_by(slug=ticket.product_slug).first() if ticket.product_slug else None
        name = product.name_in(lang_code) if product else ticket.product_name
        label = name + (f" ({ticket.variant})" if ticket.variant else "")
        result.append({
            "type_name": label,
            "qr_code_url": f"cid:{_ticket_qr_content_id(ticket)}",
            "code": ticket.code,
        })
    return result


def _static_file_bytes(relative_path):
    """Lê um ficheiro de static/ diretamente do disco (não por HTTP) --
    usado para anexar o logo/fundo do bilhete como imagens inline (CID),
    em vez de dependeres de um URL que só funciona com DOMAIN a sério."""
    path = os.path.join(current_app.static_folder, relative_path)
    with open(path, "rb") as f:
        return f.read()


def build_inline_image_attachments(tickets, domain):
    """Anexos "inline" (CID) do email: o logo, o fundo do bilhete, e o QR
    code de cada bilhete -- ver o comentário no topo do ficheiro sobre
    porquê CID em vez de base64/URL.

    Recebe a lista de `Ticket` a incluir, tal como _tickets_context acima
    (não a encomenda inteira) -- ver _group_tickets_by_recipient."""
    attachments = [
        {
            "filename": "logo-him-dub.png",
            "content": base64.b64encode(_static_file_bytes("img/logo-him-dub.png")).decode("ascii"),
            "content_id": "email-logo-lockup",
        },
        {
            "filename": "email-ticket-bg.jpg",
            "content": base64.b64encode(_static_file_bytes("img/email-ticket-bg.jpg")).decode("ascii"),
            "content_id": "email-ticket-bg",
        },
    ]
    for ticket in tickets:
        png_bytes = ticket_qr_png_bytes(ticket, domain)
        attachments.append({
            "filename": f"qr-{ticket.code}.png",
            "content": base64.b64encode(png_bytes).decode("ascii"),
            "content_id": _ticket_qr_content_id(ticket),
        })
    return attachments


def build_ticket_attachments(tickets, domain, lang_code):
    """Um anexo PDF por bilhete -- o template grafico do HIM Dub (tipo de
    bilhete correto) com o QR, o nome do comprador e o código já
    sobrepostos, pronto para imprimir, mostrar à entrada, ou carregar
    numa plataforma de revenda como a TicketSwap (que exige PDF, não
    aceita imagem). Bilhetes sem template correspondente (ver
    ticket_image.py) são simplesmente ignorados -- o email continua a
    funcionar na mesma, só com o bloco HTML com o QR simples para esses.

    Recebe a lista de `Ticket` a incluir, tal como _tickets_context acima
    (não a encomenda inteira) -- ver _group_tickets_by_recipient."""
    attachments = []
    for ticket in tickets:
        pdf_bytes = generate_ticket_image_bytes(ticket, domain, lang_code)
        if not pdf_bytes:
            continue
        attachments.append({
            "filename": f"bilhete-{ticket.code}.pdf",
            "content": base64.b64encode(pdf_bytes).decode("ascii"),
        })
    return attachments


def build_ticket_email_html(order, domain, tickets=None, recipient_name=None):
    """Renderiza templates/email/ticket_confirmation.html (o template
    gráfico desenhado pelo João) com todo o contexto -- textos no idioma da
    encomenda, bilhetes/QRs, resumo da encomenda, e os URLs absolutos das
    imagens (logo + foto de fundo do bilhete), que têm de ser um URL a
    sério (não base64) para funcionarem no Gmail/Outlook.

    `tickets`: quais bilhetes mostrar/anexar neste email em concreto --
    por omissão todos os da encomenda (`order.tickets`), mas quando a
    encomenda é enviada em vários emails separados (ver
    _group_tickets_by_recipient, mais abaixo) é só o subconjunto desse
    destinatário, para cada pessoa só ver o seu próprio QR.
    `recipient_name`: nome a usar na saudação ("Obrigado, {name}!") --
    por omissão order.name (quem comprou); quando um bilhete tem email
    próprio (Ticket.holder_email), usa-se antes o nome desse bilhete (ver
    _group_tickets_by_recipient)."""
    lang_code = order.lang_code or "pt"
    ticket_list = order.tickets if tickets is None else tickets
    s = dict(_strings(order))  # copia -- vamos preencher {name}/{id} sem mexer no dict partilhado
    s["greeting"] = s["greeting"].format(name=(recipient_name or order.name) or s["greeting_fallback"])
    s["confirmed"] = s["confirmed"].format(id=order.id)

    # Usa o template diretamente (em vez de render_template) para não
    # disparar os context processors do site inteiro -- em particular o do
    # carrinho (blueprints.shop.inject_cart), que precisa de uma sessão/
    # request HTTP a sério e por isso rebenta quando este email é montado
    # fora de um pedido (ex: resend_ticket.py, que só tem app_context).
    template = current_app.jinja_env.get_template("email/ticket_confirmation.html")
    return template.render(
        lang_code=lang_code,
        s=s,
        logo_lockup_image_url="cid:email-logo-lockup",
        ticket_bg_image_url="cid:email-ticket-bg",
        festival_dates=FESTIVAL_DATES_BY_LANG.get(lang_code, FESTIVAL_DATES_BY_LANG["pt"]),
        festival_venue=FESTIVAL_VENUE_BY_LANG.get(lang_code, FESTIVAL_VENUE_BY_LANG["pt"]),
        tickets=_tickets_context(ticket_list, lang_code),
        items=_order_items_context(order),
        processing_fee=order_processing_fee_cents(order.items) / 100,
        order_total=order.total,
        terms_url=f"{domain}{_localized_path('/infos/faq', lang_code)}",
        terms_url_label=s["terms_link_text"],
        contact_url=f"{domain}{_localized_path('/infos/faq', lang_code)}",
        # Dados legais da empresa (nome, NIF, morada) para o rodapé -- ainda
        # por preencher (o João tem de os dar); o template já sabe omitir
        # esta linha do rodapé enquanto company_legal_name estiver vazio.
        company_legal_name=None,
        company_nif=None,
        company_address=None,
    )


def _group_tickets_by_recipient(order):
    """Agrupa os bilhetes desta encomenda pelo destinatário efetivo do
    email -- pedido explícito do João: normalmente todos os bilhetes vão
    juntos, num só email, para order.email (como sempre foi), mas um
    bilhete com `Ticket.holder_email` preenchido (ver edit_order_name em
    blueprints/admin.py) sai à parte, só para essa pessoa, com só o(s)
    seu(s) bilhete(s) em anexo -- nunca com os bilhetes de outra pessoa.

    Devolve uma lista de (email, nome, lista_de_tickets), na ordem em que
    cada destinatário aparece pela primeira vez entre os bilhetes da
    encomenda. `nome` é order.name para o grupo de order.email, ou o
    `holder_name` do bilhete para um grupo com email próprio (cai para
    order.name se esse bilhete não tiver nome próprio gravado)."""
    groups = {}
    order_of_emails = []
    for ticket in order.tickets:
        email = (ticket.holder_email or "").strip() or order.email
        if email not in groups:
            groups[email] = []
            order_of_emails.append(email)
        groups[email].append(ticket)

    result = []
    for email in order_of_emails:
        group_tickets = groups[email]
        if email == order.email:
            name = order.name
        else:
            name = group_tickets[0].holder_name or order.name
        result.append((email, name, group_tickets))
    return result


def _ticket_email_payload(order, config, recipient_email, recipient_name, tickets):
    """Envia UM email (para um único destinatário, com só os seus
    bilhetes) -- usado por send_ticket_email, abaixo, uma vez por cada
    grupo devolvido por _group_tickets_by_recipient."""
    domain = config["DOMAIN"]
    s = _strings(order)
    html = build_ticket_email_html(order, domain, tickets, recipient_name)
    # anexos "a sério" (o PDF de cada bilhete, para imprimir/TicketSwap) +
    # anexos inline (logo/fundo/QR embutidos no corpo do email via CID --
    # ver comentário no topo do ficheiro).
    attachments = build_ticket_attachments(tickets, domain, order.lang_code) + build_inline_image_attachments(
        tickets, domain
    )

    payload = {
        "from": config["RESEND_FROM_EMAIL"],
        "to": [recipient_email],
        "subject": s["subject"].format(id=order.id),
        "html": html,
    }
    if attachments:
        payload["attachments"] = attachments

    return payload


def _send_ticket_email_to(order, config, recipient_email, recipient_name, tickets):
    payload = _ticket_email_payload(order, config, recipient_email, recipient_name, tickets)
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {config['RESEND_API_KEY']}"},
            json=payload,
            timeout=20,
        )
        if response.status_code >= 300:
            # Antes isto falhava em silencio -- agora fica no terminal onde
            # correste "py app.py", para se saber logo porque e que o
            # Resend recusou o envio (dominio nao verificado, "to" fora do
            # permitido em modo sandbox, chave invalida, etc).
            logger.warning(
                "Resend recusou o envio do email da encomenda #%s (status %s)",
                order.id, response.status_code,
            )
        return response.status_code < 300
    except requests.RequestException as exc:
        logger.warning("Falha de rede a enviar o email da encomenda #%s", order.id)
        return False


def send_ticket_email(order, config):
    """Envia o email de confirmação com os bilhetes, no idioma em que o
    cliente fez o checkout (order.lang_code).

    Normalmente é um único email, para order.email, com todos os bilhetes
    -- mas se algum bilhete tiver um email próprio gravado
    (`Ticket.holder_email`, ver edit_order_name em blueprints/admin.py),
    esse bilhete sai num email à parte, só para essa pessoa (pedido
    explícito do João, para dar para reencaminhar um bilhete a sério para
    quem o vai usar, em vez de ir sempre só para quem comprou).

    Devolve True só se TODOS os emails desta encomenda foram enviados com
    sucesso -- False em modo demo (sem RESEND_API_KEY), ou se pelo menos um
    falhou (ver os logs do servidor para saber qual e porquê)."""
    if not config.get("EMAILS_LIVE"):
        return False  # modo demo -- os bilhetes ja foram gerados, so nao ha envio a serio

    groups = _group_tickets_by_recipient(order)
    if not groups and order.email:
        groups = [(order.email, order.name, [])]

    all_ok = True
    for recipient_email, recipient_name, tickets in groups:
        if not _send_ticket_email_to(order, config, recipient_email, recipient_name, tickets):
            all_ok = False
    return all_ok


# Quem recebe o aviso de "faltam poucos bilhetes para um lote acabar" (ver
# batches.py check_and_flag_low_stock_alert) -- pedido explícito do João.
LOW_STOCK_ALERT_RECIPIENTS = ["decibelcostume@gmail.com"]


def send_low_stock_alert_email(subject, html_body, config):
    """Email simples (sem template gráfico nem anexos) para avisar a
    organização quando um lote de bilhetes está quase a esgotar. Mesmo
    "modo demo" que send_ticket_email: sem RESEND_API_KEY configurada não
    envia nada a sério (o aviso continua visível no admin de qualquer
    forma -- ver templates/admin/orders.html)."""
    if not config.get("EMAILS_LIVE"):
        return False

    payload = {
        "from": config["RESEND_FROM_EMAIL"],
        "to": LOW_STOCK_ALERT_RECIPIENTS,
        "subject": subject,
        "html": html_body,
    }
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {config['RESEND_API_KEY']}"},
            json=payload,
            timeout=20,
        )
        if response.status_code >= 300:
            logger.warning(
                "Resend recusou o envio do alerta de stock baixo (status %s)",
                response.status_code,
            )
        return response.status_code < 300
    except requests.RequestException as exc:
        logger.warning("Falha de rede a enviar o alerta de stock baixo")
        return False
