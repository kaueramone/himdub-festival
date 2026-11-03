"""Gera a imagem final de cada bilhete: o template grafico do HIM Dub
(um ficheiro por tipo de bilhete, em `assets/ticket_templates/`) com o QR
code, o tipo de bilhete, o nome do comprador e o codigo do bilhete
sobrepostos -- pronta a anexar ao email de confirmacao (ver emailing.py).

Os bounding boxes do quadrado onde entra o QR foram medidos uma unica vez
sobre os ficheiros de template (deteção automática por diferença de cor +
componentes conexas, script auxiliar fora do projeto) e ficam fixos aqui
porque os templates em si não mudam de tamanho.
"""

import io
import os

import qrcode
from PIL import Image, ImageDraw, ImageFont

from models import OrderItem, Product
from ticketing import ticket_checkin_url

_HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(_HERE, "assets", "ticket_templates")
FONT_REGULAR = os.path.join(_HERE, "static", "fonts", "Abel-Regular.ttf")

# slug do produto -> ficheiro de template + bbox (x0, y0, x1, y1) do
# quadrado onde entra o QR, medido na resolução nativa de cada ficheiro
# (todos os templates estão a 1754x1550 -- o caravan_pass original vinha a
# 2x, foi reduzido para o mesmo tamanho dos outros para o projeto não ficar
# com um zip gigante; qualidade a essa resolução continua ótima tanto para
# email como para impressão). Atualizado com os templates de 21/09 (design
# novo com o bilhete de acessibilidade a ter template próprio "Bese Saka").
_TEMPLATES = {
    "passe-caravan": {
        "file": os.path.join(ASSETS_DIR, "caravan_pass.jpg"),
        "box": (960, 27, 1265, 400),
    },
    "bilhete-geral": {
        "file": os.path.join(ASSETS_DIR, "full_pass.jpg"),
        "box": (965, 95, 1266, 400),
    },
    "bilhete-fim-de-semana": {
        "file": os.path.join(ASSETS_DIR, "weekend_pass.jpg"),
        "box": (970, 125, 1265, 425),
    },
    "bilhete-familia": {
        "file": os.path.join(ASSETS_DIR, "family_ticket.jpg"),
        "box": (965, 95, 1266, 400),
    },
    "bilhete-acessibilidade": {
        "file": os.path.join(ASSETS_DIR, "accessibility_ticket.jpg"),
        "box": (970, 125, 1265, 425),
    },
    "full-pass-caravan": {
        "file": os.path.join(ASSETS_DIR, "full_pass_caravan.jpg"),
        "box": (970, 95, 1266, 400),
    },
    # Bilhetes de cortesia (emitidos so pela area de admin -- ver
    # blueprints/admin.py "Emitir bilhete"). O "Guest Full Pass" tem arte
    # propria; os 3 embaixadores (Argentina/Burkina Faso/Irlanda) partilham
    # a mesma arte generica "Ambassador Ticket" -- o nome do produto entra
    # no rodape do bilhete (coluna "TIPO DE BILHETE"), por isso nao precisa
    # de arte por pais.
    "vip-guest": {
        "file": os.path.join(ASSETS_DIR, "guest_full_pass.jpg"),
        "box": (972, 126, 1266, 367),
    },
    "embaixador-argentina": {
        "file": os.path.join(ASSETS_DIR, "ambassador_ticket.jpg"),
        "box": (972, 126, 1266, 367),
    },
    "embaixador-burkina-faso": {
        "file": os.path.join(ASSETS_DIR, "ambassador_ticket.jpg"),
        "box": (972, 126, 1266, 367),
    },
    "embaixador-irlanda": {
        "file": os.path.join(ASSETS_DIR, "ambassador_ticket.jpg"),
        "box": (972, 126, 1266, 367),
    },
}

# Templates alternativos para lotes seguintes (ver batches.py) -- chave é
# (slug, indice_do_lote). Só é preciso pôr aqui os lotes que têm arte
# própria; um bilhete de um lote sem entrada aqui usa o template "normal"
# de _TEMPLATES acima (mesma caixa/posição do QR, só muda o preço).
#
# O 3º lote (Ananse Ntontan, 220€) e o 4º lote (Djwennimmen, 280€) ainda
# não têm arte própria -- por decisão do João, usam por agora a mesma arte
# do 2º lote (Duage) como placeholder. Assim que ele mandar os templates
# definitivos, basta acrescentar aqui as entradas
# ("bilhete-geral", 3) / ("bilhete-geral", 4) (e o mesmo para
# "full-pass-caravan") a apontar para os ficheiros novos.
_TEMPLATES_BY_TIER = {
    ("bilhete-geral", 2): {
        "file": os.path.join(ASSETS_DIR, "full_pass_2nd.jpg"),
        "box": (970, 95, 1266, 400),
    },
    ("bilhete-geral", 3): {
        "file": os.path.join(ASSETS_DIR, "full_pass_2nd.jpg"),  # placeholder -- ainda é a arte Duage
        "box": (970, 95, 1266, 400),
    },
    ("bilhete-geral", 4): {
        "file": os.path.join(ASSETS_DIR, "full_pass_2nd.jpg"),  # placeholder -- ainda é a arte Duage
        "box": (970, 95, 1266, 400),
    },
    ("full-pass-caravan", 2): {
        "file": os.path.join(ASSETS_DIR, "full_pass_caravan_2nd.jpg"),
        "box": (970, 95, 1266, 400),
    },
    ("full-pass-caravan", 3): {
        "file": os.path.join(ASSETS_DIR, "full_pass_caravan_2nd.jpg"),  # placeholder -- ainda é a arte Duage
        "box": (970, 95, 1266, 400),
    },
    ("full-pass-caravan", 4): {
        "file": os.path.join(ASSETS_DIR, "full_pass_caravan_2nd.jpg"),  # placeholder -- ainda é a arte Duage
        "box": (970, 95, 1266, 400),
    },
}

# Faixa creme em baixo do template onde entram tipo/nome/código (medida
# como fração da altura, para funcionar em qualquer resolução).
_FOOTER_TOP_FRAC = 1252 / 1550
_LABEL_COLOR = "#8a8368"
_VALUE_COLOR = "#33301f"

_FOOTER_STRINGS = {
    "pt": ("TIPO DE BILHETE", "NOME", "CÓDIGO"),
    "en": ("TICKET TYPE", "NAME", "CODE"),
    "fr": ("TYPE DE BILLET", "NOM", "CODE"),
}

# 4ª linha, opcional, só para os bilhetes de Acessibilidade (acompanhante)
# e Família (crianças) -- ver companion_name/kids_names em models.py.
# Fica centrada por baixo das 3 colunas normais, dentro da mesma faixa
# creme (ver _FOOTER_TOP_FRAC) -- não precisa de arte nova nos templates
# porque essa faixa já vem lisa/sem texto impresso.
_EXTRA_LINE_LABELS = {
    "pt": {"companion": "ACOMPANHANTE", "kids": "CRIANÇAS"},
    "en": {"companion": "COMPANION", "kids": "KIDS"},
    "fr": {"companion": "ACCOMPAGNANT", "kids": "ENFANTS"},
}


def _product_slug_for_ticket(ticket):
    """Slug do bilhete "a sério" que este Ticket representa. Bilhetes
    gerados depois da introdução dos pacotes/lotes já guardam isto
    diretamente (`ticket.product_slug` -- necessário porque um pacote como
    "Full Pass + Caravan" gera 2 Tickets a partir da mesma linha de
    encomenda, cada um para um produto diferente). Para bilhetes antigos
    sem essa coluna preenchida, cai no comportamento anterior: vai buscar
    o slug através do OrderItem original."""
    if getattr(ticket, "product_slug", None):
        return ticket.product_slug

    item = OrderItem.query.get(ticket.order_item_id)
    if not item or not item.product_id:
        return None
    product = Product.query.get(item.product_id)
    return product.slug if product else None


def _template_for_ticket(ticket, slug):
    """Escolhe o template certo tendo em conta o lote do bilhete (ver
    batches.py) -- ex: um "Bilhete Geral" do 2º lote usa a arte "Duage"
    em vez da arte "Early Bird", mesmo sendo o mesmo tipo de bilhete."""
    tier = getattr(ticket, "batch_tier", None)
    if tier:
        info = _TEMPLATES_BY_TIER.get((slug, tier))
        if info:
            return info
    return _TEMPLATES.get(slug)


def _fit_text(draw, text, font_path, start_size, max_width, min_size=14):
    """Devolve uma ImageFont que cabe em max_width, encolhendo a partir de
    start_size se necessário (nomes compridos, códigos, etc)."""
    size = start_size
    font = ImageFont.truetype(font_path, size)
    while draw.textlength(text, font=font) > max_width and size > min_size:
        size -= 2
        font = ImageFont.truetype(font_path, size)
    return font


def generate_ticket_image_bytes(ticket, domain, lang_code="pt"):
    """Devolve os bytes PDF do bilhete (template + QR + tipo/nome/código),
    ou None se não houver template para o produto deste bilhete (nesse
    caso o email cai só no bloco HTML com o QR simples, como antes)."""
    slug = _product_slug_for_ticket(ticket)
    info = _template_for_ticket(ticket, slug)
    if not info or not os.path.exists(info["file"]):
        return None

    im = Image.open(info["file"]).convert("RGB")
    w, h = im.size
    box = info["box"]

    url = ticket_checkin_url(ticket, domain)
    box_w = box[2] - box[0]
    box_h = box[3] - box[1]
    qr_size = int(min(box_w, box_h) * 0.80)

    qr = qrcode.QRCode(border=1, box_size=10)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)

    cx = (box[0] + box[2]) // 2
    cy = (box[1] + box[3]) // 2
    qr_x = cx - qr_size // 2
    qr_y = cy - qr_size // 2

    draw = ImageDraw.Draw(im)
    # pequena moldura branca a volta do QR, para se destacar do fundo creme
    pad = int(qr_size * 0.06)
    draw.rectangle(
        [qr_x - pad, qr_y - pad, qr_x + qr_size + pad, qr_y + qr_size + pad],
        fill="white",
    )
    im.paste(qr_img, (qr_x, qr_y))

    # --- faixa creme em baixo: tipo de bilhete / nome / código, 3 colunas ---
    footer_top = int(h * _FOOTER_TOP_FRAC)
    footer_bottom = h
    scale = w / 1754  # os templates de 1754x1700 são a base; caravan_pass é 2x

    label_type, label_name, label_code = _FOOTER_STRINGS.get(lang_code, _FOOTER_STRINGS["pt"])
    ticket_type_label = ticket.product_name + (f" ({ticket.variant})" if ticket.variant else "")
    # Nome deste bilhete em concreto (ver Ticket.holder_name em models.py),
    # se tiver sido pedido no checkout -- cada um dos vários bilhetes de
    # uma mesma compra pode ter uma pessoa diferente. Cai no nome de quem
    # comprou para bilhetes de Acessibilidade/Família (que não usam
    # holder_name) e para bilhetes antigos, gerados antes desta coluna
    # existir.
    buyer_name = (getattr(ticket, "holder_name", "") or "").strip() or (ticket.order.name or "").strip() or "-"

    columns = [
        (label_type, ticket_type_label),
        (label_name, buyer_name),
        (label_code, ticket.code),
    ]
    col_centers = [w * 0.20, w * 0.50, w * 0.80]
    col_max_width = w * 0.30
    band_center_y = (footer_top + footer_bottom) // 2

    font_label_size = int(22 * scale)
    font_value_size = int(34 * scale)

    for (label, value), col_x in zip(columns, col_centers):
        font_label = _fit_text(draw, label, FONT_REGULAR, font_label_size, col_max_width)
        lw = draw.textlength(label, font=font_label)
        draw.text((col_x - lw / 2, band_center_y - 34 * scale), label, font=font_label, fill=_LABEL_COLOR)

        font_value = _fit_text(draw, value, FONT_REGULAR, font_value_size, col_max_width)
        vw = draw.textlength(value, font=font_value)
        draw.text((col_x - vw / 2, band_center_y + 2 * scale), value, font=font_value, fill=_VALUE_COLOR)

    # --- 4ª linha opcional: acompanhante (Acessibilidade) ou crianças
    # (Família) -- ver _EXTRA_LINE_LABELS acima. Só um dos dois campos
    # vem preenchido, nunca os dois no mesmo bilhete (ver checkout() em
    # blueprints/shop.py). Centrada por baixo das 3 colunas, mais larga
    # (70% da largura) porque não está dividida em colunas.
    extra_labels = _EXTRA_LINE_LABELS.get(lang_code, _EXTRA_LINE_LABELS["pt"])
    extra_label = extra_value = None
    if getattr(ticket, "companion_name", ""):
        extra_label, extra_value = extra_labels["companion"], ticket.companion_name
    elif getattr(ticket, "kids_names", ""):
        extra_label, extra_value = extra_labels["kids"], ticket.formatted_kids_names

    if extra_label and extra_value:
        extra_max_width = w * 0.7
        extra_center_x = w * 0.5

        font_label = _fit_text(draw, extra_label, FONT_REGULAR, font_label_size, extra_max_width)
        lw = draw.textlength(extra_label, font=font_label)
        draw.text((extra_center_x - lw / 2, band_center_y + 48 * scale), extra_label, font=font_label, fill=_LABEL_COLOR)

        font_value = _fit_text(draw, extra_value, FONT_REGULAR, font_value_size, extra_max_width)
        vw = draw.textlength(extra_value, font=font_value)
        draw.text((extra_center_x - vw / 2, band_center_y + 76 * scale), extra_value, font=font_value, fill=_VALUE_COLOR)

    # PDF em vez de PNG -- a imagem é sobretudo fotografica (o template),
    # por isso o PNG fica gigante (4-5MB por bilhete); o PIL guarda a
    # imagem dentro do PDF com a mesma compressão JPEG (quality=90), por
    # isso o tamanho fica igual ao que era em .jpg (~900KB), mas agora o
    # ficheiro é mesmo um PDF de 1 página -- necessário para compatibilidade
    # com plataformas de revenda tipo TicketSwap, que exigem PDF.
    buf = io.BytesIO()
    im.save(buf, format="PDF", resolution=150.0, quality=90,
            creationDate=ticket.order.created_at.utctimetuple(), modDate=ticket.order.created_at.utctimetuple())
    return buf.getvalue()
