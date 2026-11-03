"""Geração dos bilhetes individuais (com código/QR) de uma encomenda paga.

Cada unidade comprada de um produto da categoria "ticket" (bilhetes de
entrada -- não o merchandise nem o glamping) gera um `Ticket` com um código
único. Esse código é o que vai codificado no QR enviado por email, e o que
o porteiro associa ao número da pulseira cashless GoFun à entrada.
"""

import io
import secrets

import qrcode

from extensions import db
from models import OrderItem, Product, Ticket, decode_ticket_names


def _new_ticket_code():
    """Código curto e legível à mão (para o caso de o QR falhar e o porteiro
    ter de o escrever à mão), com baixíssima probabilidade de colisão."""
    return secrets.token_hex(16).upper()  # ex: "A1B2C3D4E5"


def generate_tickets_for_order(order):
    """Cria um Ticket por cada unidade de bilhete (categoria "ticket") na
    encomenda -- inclusive para "pacotes" como Full Pass + Caravan (ver
    batches.py BUNDLES), que geram UM SÓ bilhete/QR combinado, tal como
    escolhido. O QR desse bilhete dá acesso a ambas as coisas (entrada do
    festival + parque de caravanas); o `product_slug` fica com o slug do
    pacote em si, mas conta para o limite do Full Pass na mesma (ver
    batches.sold_count, que sabe atribuir vendas de pacotes ao lote do
    componente que incluem).

    Completa apenas as unidades em falta. O chamador bloqueia a encomenda
    durante a transação, impedindo emissões concorrentes duplicadas.
    """
    created = []
    for item in order.items:
        product = Product.query.get(item.product_id)
        if not product or product.category != "ticket":
            continue  # merchandise e glamping não geram bilhete/QR

        # Um nome por unidade, na mesma ordem (ver OrderItem.ticket_names em
        # models.py) -- lista vazia para linhas que não são deste tipo de
        # bilhete (Acessibilidade/Família continuam a usar companion_name/
        # kids_names, abaixo).
        names = decode_ticket_names(item.ticket_names)
        # Email por unidade, na mesma ordem -- ver OrderItem.ticket_emails em
        # models.py (reaproveita o mesmo descodificador de lista JSON, que é
        # genérico apesar do nome). Normalmente vazio -- só é preenchido mais
        # tarde, à mão, no admin (ver edit_order_name em blueprints/admin.py).
        emails = decode_ticket_names(item.ticket_emails)

        existing = Ticket.query.filter_by(order_item_id=item.id).count()
        if existing > item.quantity:
            raise ValueError("Order item has more tickets than purchased units")
        for index in range(existing, item.quantity):
            code = _new_ticket_code()
            while Ticket.query.filter_by(code=code).first():  # colisão (muitíssimo raro)
                code = _new_ticket_code()

            ticket = Ticket(
                code=code,
                order_id=order.id,
                order_item_id=item.id,
                product_name=item.product_name,
                variant=item.variant,
                product_slug=product.slug,
                batch_tier=item.batch_tier,
                # Só preenchidos para bilhetes de Acessibilidade/Família
                # (ver OrderItem em models.py) -- copiados para o porteiro
                # ver isto no check-in sem precisar de ir buscar o
                # OrderItem à parte (ver templates/checkin/ticket.html).
                companion_name=item.companion_name,
                kids_names=item.kids_names,
                # Nome de quem vai usar este bilhete em concreto (ver
                # OrderItem.ticket_names, acima) -- vazio se esta linha não
                # tiver nomes por bilhete gravados.
                holder_name=names[index] if index < len(names) else "",
                # Email de quem vai usar este bilhete em concreto (ver
                # OrderItem.ticket_emails, acima) -- normalmente vazio, o que
                # significa "continua a ir para order.email", como sempre foi.
                holder_email=emails[index] if index < len(emails) else "",
            )
            db.session.add(ticket)
            created.append(ticket)

    return created


def ticket_checkin_url(ticket, domain):
    return f"{domain}/checkin/{ticket.code}"


def ticket_qr_png_bytes(ticket, domain):
    """Gera o PNG do QR code de um bilhete (aponta para a página de
    check-in do bilhete, lida pelo scanner do porteiro à entrada)."""
    img = qrcode.make(ticket_checkin_url(ticket, domain), box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
