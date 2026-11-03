"""Reenvia (ou testa) o email de confirmacao com os bilhetes de uma
encomenda existente -- util para:

  1. Diagnosticar porque e que um email falhou (mostra a resposta exata
     que o Resend devolveu, em vez de ficar tudo em silencio).
  2. Reenviar o email a um cliente sem teres de recriar a encomenda.

Uso:
    py resend_ticket.py            # reenvia a encomenda paga mais recente
    py resend_ticket.py 3          # reenvia a encomenda #3
"""

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from app import create_app
from emailing import send_ticket_email, _group_tickets_by_recipient
from extensions import db
from models import Order


def main():
    order_id = int(sys.argv[1]) if len(sys.argv) > 1 else None

    app = create_app()
    with app.app_context():
        if order_id:
            order = Order.query.get(order_id)
        else:
            order = (
                Order.query.filter(Order.status.in_(["paid", "demo-paid"]))
                .order_by(Order.id.desc())
                .first()
            )

        if not order:
            print("Nenhuma encomenda encontrada.")
            return

        print(f"Encomenda #{order.id} -- {order.name} <{order.email}> -- status={order.status}")
        print(f"EMAILS_LIVE (RESEND_API_KEY configurada?) = {app.config['EMAILS_LIVE']}")
        print(f"RESEND_FROM_EMAIL = {app.config['RESEND_FROM_EMAIL']!r}")
        # Normalmente um só destinatário (order.email) -- mas se algum
        # bilhete tiver email próprio (ver edit_order_name em
        # blueprints/admin.py), a encomenda vai em vários emails separados
        # (ver _group_tickets_by_recipient em emailing.py).
        groups = _group_tickets_by_recipient(order)
        if len(groups) > 1:
            print(f"A enviar em {len(groups)} emails separados:")
            for recipient_email, recipient_name, tickets in groups:
                print(f"  - {recipient_email!r} ({recipient_name}) -- {len(tickets)} bilhete(s)")
        else:
            print(f"A enviar para: {order.email!r}")
        print(f"Bilhetes desta encomenda: {len(order.tickets)}")
        print("---")

        if not app.config["EMAILS_LIVE"]:
            print("RESEND_API_KEY vazia no .env -- isto ia correr em modo demo (não envia a sério).")
            return

        sent = send_ticket_email(order, app.config)
        print(f"Resultado: {'ENVIADO com sucesso' if sent else 'FALHOU'}")
        if not sent:
            print("Vê a mensagem 'Resend recusou o envio...' ou 'Falha de rede...' que devia ter")
            print("aparecido em cima (vem do logger, não de um print) -- tem o motivo exato.")
        else:
            order.email_sent = True
            db.session.commit()
            print("order.email_sent atualizado para True na base de dados.")


if __name__ == "__main__":
    main()
