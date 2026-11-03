"""Pequenas migrações "manuais" para bases de dados SQLite já existentes.

Este projeto não usa Flask-Migrate/Alembic (é propositadamente simples), o
que normalmente está bem porque `db.create_all()` cria tudo de raiz numa
base de dados nova. O problema é quando já existem encomendas reais e
acrescentamos colunas novas aos modelos (ex: o sistema de lotes) -- nesse
caso `create_all()` NÃO adiciona colunas a tabelas que já existem, por
isso fazemos aqui um ALTER TABLE manual, seguro para correr sempre no
arranque (só mexe em colunas que ainda não existem).
"""

from sqlalchemy import inspect, text

from extensions import db


def _ensure_column(conn, inspector, table, column, ddl_type):
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column not in existing:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
        return True
    return False


def run(app):
    with app.app_context():
        inspector = inspect(db.engine)
        with db.engine.connect() as conn:
            _ensure_column(conn, inspector, "orders", "reservation_expires_at", "TIMESTAMP")
            _ensure_column(conn, inspector, "tickets", "product_slug", "VARCHAR(120) DEFAULT ''")
            _ensure_column(conn, inspector, "tickets", "batch_tier", "INTEGER")
            _ensure_column(conn, inspector, "order_items", "batch_tier", "INTEGER")
            _ensure_column(conn, inspector, "orders", "nif", "VARCHAR(20) DEFAULT ''")
            _ensure_column(conn, inspector, "order_items", "companion_name", "VARCHAR(120) DEFAULT ''")
            _ensure_column(conn, inspector, "order_items", "kids_names", "TEXT DEFAULT ''")
            _ensure_column(conn, inspector, "tickets", "companion_name", "VARCHAR(120) DEFAULT ''")
            _ensure_column(conn, inspector, "tickets", "kids_names", "TEXT DEFAULT ''")
            _ensure_column(conn, inspector, "tickets", "previous_codes", "TEXT DEFAULT ''")
            _ensure_column(conn, inspector, "order_items", "ticket_names", "TEXT DEFAULT ''")
            _ensure_column(conn, inspector, "tickets", "holder_name", "VARCHAR(120) DEFAULT ''")
            _ensure_column(conn, inspector, "order_items", "ticket_emails", "TEXT DEFAULT ''")
            _ensure_column(conn, inspector, "tickets", "holder_email", "VARCHAR(180) DEFAULT ''")
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_reservation_expires_at ON orders (reservation_expires_at)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_wristband_nonempty ON tickets (wristband_code) WHERE wristband_code IS NOT NULL AND wristband_code <> ''"))
            conn.commit()

        # Preenche product_slug em bilhetes antigos (gerados antes desta
        # coluna existir), a partir do produto do OrderItem original.
        from models import Ticket, OrderItem, Product

        missing = Ticket.query.filter(
            (Ticket.product_slug.is_(None)) | (Ticket.product_slug == "")
        ).all()
        if missing:
            for ticket in missing:
                item = OrderItem.query.get(ticket.order_item_id)
                product = Product.query.get(item.product_id) if item else None
                if product:
                    ticket.product_slug = product.slug
            db.session.commit()
