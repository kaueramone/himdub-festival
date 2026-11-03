"""Regression tests with synthetic buyers, SQLite and all external HTTP blocked."""
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEST_DATABASE = os.environ.get("HIMDUB_TEST_DATABASE_URL", "sqlite:///:memory:")
if not TEST_DATABASE.startswith("sqlite:") and not TEST_DATABASE.endswith("/himdub_test"):
    raise RuntimeError("PostgreSQL tests require a dedicated database named himdub_test")
os.environ.update(DATABASE_URL=TEST_DATABASE, SECRET_KEY="isolated-purchase-test-key",
                  STRIPE_SECRET_KEY="", STRIPE_PUBLISHABLE_KEY="", STRIPE_WEBHOOK_SECRET="",
                  RESEND_API_KEY="", ADMIN_PASSWORD="isolated-admin", CHECKIN_PASSWORD="isolated-checkin",
                  SITE_LOCK_PASSWORD="", COMING_SOON_MODE="off", DOMAIN="http://localhost",
                  RENDER="", APP_ENV="development", CDN_BASE_URL="")
import dotenv
dotenv.load_dotenv = lambda *a, **k: None
import app as app_module
from extensions import db
from models import Order, OrderItem, Product, Ticket, EmailDelivery
from blueprints import shop


class PurchaseSecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app
        self.app.config.update(TESTING=True, PAYMENTS_LIVE=False, EMAILS_LIVE=False,
                               STRIPE_SECRET_KEY="sk_test_isolated", STRIPE_WEBHOOK_SECRET="isolated-hook")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        from db_migrate import run
        run(self.app)
        db.session.add(Product(id=1, slug="test-entry", category="ticket", name="Test", price_cents=1000))
        db.session.add(Product(id=2, slug="test-shirt", category="merch", name="Shirt", price_cents=1000, variants="S,M"))
        db.session.commit()
        self.client = self.app.test_client()
        self.http = patch("requests.sessions.Session.request", side_effect=AssertionError("External HTTP forbidden"))
        self.http.start()
        self.alerts = patch.object(shop, "_check_low_stock_alerts")
        self.alerts.start()

    def tearDown(self):
        self.http.stop()
        self.alerts.stop()
        db.session.remove()
        self.ctx.pop()

    def post(self, path, data=None, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            session["csrf_token"] = "isolated-csrf"
        return client.post(path, data={**(data or {}), "csrf_token": "isolated-csrf"})

    def order(self, status="pending", quantity=1):
        order = Order(name="Synthetic Buyer", email="buyer@example.invalid", status=status,
                      stripe_session_id="cs_isolated", total_cents=1000 * quantity)
        order.items.append(OrderItem(product_id=1, product_name="Test", price_cents=1000, quantity=quantity))
        db.session.add(order)
        db.session.commit()
        return order

    def webhook(self, client=None, event_type="checkout.session.completed", **changes):
        obj = dict(id="cs_isolated", payment_status="paid", amount_total=1000,
                   currency="eur", mode="payment", livemode=False)
        obj.update(changes)
        body = json.dumps({"type": event_type, "data": {"object": obj}})
        timestamp = str(int(time.time()))
        signature = hmac.new(b"isolated-hook", (timestamp + "." + body).encode(), hashlib.sha256).hexdigest()
        return (client or self.client).post("/webhooks/stripe", data=body, content_type="application/json",
                                headers={"Stripe-Signature": f"t={timestamp},v1={signature}"})

    def test_unsigned_webhook_rejected_even_without_configuration(self):
        order = self.order()
        self.app.config["STRIPE_WEBHOOK_SECRET"] = ""
        self.assertEqual(self.client.post("/webhooks/stripe", json={}).status_code, 503)
        self.assertEqual(db.session.get(Order, order.id).status, "pending")

    def test_unpaid_signed_event_does_not_issue(self):
        order = self.order()
        self.assertEqual(self.webhook(payment_status="unpaid").status_code, 200)
        self.assertEqual(db.session.get(Order, order.id).status, "processing")
        self.assertEqual(Ticket.query.count(), 0)

    def test_payment_amount_currency_and_environment_must_match(self):
        order = self.order()
        for change in ({"amount_total": 1}, {"currency": "usd"}, {"livemode": True}):
            self.assertEqual(self.webhook(**change).status_code, 400)
        self.assertEqual(db.session.get(Order, order.id).status, "pending")

    def test_payment_and_tickets_rollback_and_retry(self):
        order = self.order()
        with patch.object(shop, "generate_tickets_for_order", side_effect=RuntimeError("Synthetic failure")):
            self.assertEqual(self.webhook().status_code, 500)
        self.assertEqual(db.session.get(Order, order.id).status, "pending")
        self.assertEqual(self.webhook().status_code, 200)
        self.assertEqual(self.webhook().status_code, 200)
        self.assertEqual(Ticket.query.count(), 1)

    def test_partial_ticket_generation_can_be_repaired(self):
        order = self.order(status="paid", quantity=2)
        db.session.add(Ticket(code="EXISTING", order_id=order.id, order_item_id=order.items[0].id))
        db.session.commit()
        self.assertEqual(self.webhook(amount_total=2000).status_code, 200)
        self.assertEqual(Ticket.query.count(), 2)

    def test_success_requires_scoped_signed_token(self):
        order = self.order(status="paid")
        self.assertEqual(self.client.get(f"/checkout/success/{order.id}").status_code, 404)
        with self.app.test_request_context():
            token = shop._order_token(order.id)
        response = self.client.get(f"/checkout/success/{order.id}?token={token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(self.client.get(f"/checkout/success/{order.id+1}?token={token}").status_code, 404)
        self.assertIn(f'/en/checkout/success/{order.id}?token={token}', response.get_data(as_text=True))
        self.assertEqual(self.client.get(f"/en/checkout/success/{order.id}?token={token}").status_code, 200)

    def test_forms_require_csrf(self):
        self.assertEqual(self.client.post("/cart/add", data={"product_id": 1}).status_code, 400)
        self.assertEqual(self.post("/cart/add", {"product_id": 1}).status_code, 302)

    def test_inactive_invalid_variants_and_large_quantities_rejected(self):
        for data in ({"product_id": 1, "quantity": 10000},
                     {"product_id": 2, "variant": "INVALID"}, {"product_id": 1, "variant": "S"}):
            self.assertEqual(self.post("/cart/add", data).status_code, 400)
        db.session.get(Product, 1).is_active = False
        db.session.commit()
        self.assertEqual(self.post("/cart/add", {"product_id": 1}).status_code, 400)

    def test_login_redirect_is_local(self):
        response = self.post("/admin/login?next=https://example.invalid", {"password": "isolated-admin"})
        self.assertEqual(response.location, "/admin/orders")

    def test_cancelled_ticket_cannot_check_in(self):
        order = self.order(status="cancelled")
        db.session.add(Ticket(code="CANCELLED", order_id=order.id, order_item_id=order.items[0].id))
        db.session.commit()
        self.post("/admin/login", {"password": "isolated-checkin"})
        self.assertEqual(self.post("/checkin/CANCELLED", {"wristband_code": "123"}).status_code, 403)

    def test_excel_customer_text_never_becomes_formula(self):
        from blueprints.admin import _write_sheet
        from openpyxl import Workbook, load_workbook
        wb = Workbook()
        _write_sheet(wb.active, "Test", ["Name", "Amount"], [["=1+1", 20]])
        buffer = io.BytesIO()
        wb.save(buffer)
        loaded = load_workbook(io.BytesIO(buffer.getvalue()))
        self.assertEqual(loaded.active["A2"].data_type, "s")
        self.assertEqual(loaded.active["B2"].data_type, "n")

    def test_durable_email_is_saved_with_payment_and_not_duplicated(self):
        self.order()
        self.app.config["EMAILS_LIVE"] = True
        self.assertEqual(self.webhook().status_code, 200)
        self.assertEqual(self.webhook().status_code, 200)
        self.assertEqual(EmailDelivery.query.count(), 1)
        self.assertEqual(EmailDelivery.query.one().status, "pending")

    def test_email_job_creation_failure_rolls_back_payment_and_tickets(self):
        order = self.order()
        self.app.config["EMAILS_LIVE"] = True
        with patch("outbox.enqueue_order_emails", side_effect=RuntimeError("Synthetic outbox failure")):
            self.assertEqual(self.webhook().status_code, 500)
        self.assertEqual(db.session.get(Order, order.id).status, "pending")
        self.assertEqual(Ticket.query.count(), 0)

    def test_email_retry_preserves_payload_and_idempotency_key(self):
        from outbox import process_pending
        from datetime import datetime, timedelta
        self.order()
        self.app.config.update(EMAILS_LIVE=True, RESEND_API_KEY="isolated")
        self.webhook()
        payload = {"from": "tickets@example.invalid", "to": ["buyer@example.invalid"], "subject": "Test", "html": "Test"}
        with patch("emailing._ticket_email_payload", return_value=payload), patch("outbox.requests.post") as send:
            send.return_value = Mock(status_code=503)
            process_pending(self.app)
            job = EmailDelivery.query.one()
            self.assertEqual(job.status, "pending")
            job.available_at = datetime.utcnow() - timedelta(seconds=1)
            db.session.commit()
            send.return_value = Mock(status_code=200, json=lambda: {"id": "synthetic-message"})
            process_pending(self.app)
            self.assertEqual(EmailDelivery.query.one().status, "sent")
            self.assertEqual(send.call_count, 2)
            self.assertEqual(send.call_args_list[0].kwargs["headers"]["Idempotency-Key"],
                             send.call_args_list[1].kwargs["headers"]["Idempotency-Key"])
            self.assertEqual(send.call_args_list[0].kwargs["json"], send.call_args_list[1].kwargs["json"])

    def test_reserved_last_unit_blocks_other_checkout_until_expiry(self):
        from batches import TICKET_BATCHES
        from datetime import datetime, timedelta
        with patch.dict(TICKET_BATCHES, {"test-entry": [{"limit": 1, "until": None, "price_cents": 1000}]}):
            self.assertEqual(self.post("/cart/add", {"product_id": 1}).status_code, 302)
            self.app.config["PAYMENTS_LIVE"] = True
            with patch.object(shop, "_start_stripe_checkout", return_value=("synthetic-stripe", 200)):
                response = self.post("/checkout", {"name": "Test", "email": "buyer@example.invalid", "country": "Portugal", "ticket_name__1__0": "Test"})
            self.assertEqual(response.status_code, 200)
            other = self.app.test_client()
            self.assertEqual(self.post("/cart/add", {"product_id": 1}, client=other).status_code, 409)
            order = Order.query.one()
            order.reservation_expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.session.commit()
            self.assertEqual(self.post("/cart/add", {"product_id": 1}, client=other).status_code, 302)

    def test_individual_account_revocation_invalidates_existing_session(self):
        from models import AdminUser
        from werkzeug.security import generate_password_hash
        user = AdminUser(email="staff@example.invalid", role="admin", password_hash=generate_password_hash("isolated-password"))
        db.session.add(user)
        db.session.commit()
        response = self.post("/admin/login", {"email": user.email, "password": "isolated-password"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/admin/orders").status_code, 200)
        user.active = False
        db.session.commit()
        self.assertEqual(self.client.get("/admin/orders").status_code, 302)

    def test_login_rate_limit_persists_across_browser_sessions(self):
        for _ in range(5):
            self.post("/admin/login", {"email": "staff@example.invalid", "password": "wrong"})
        response = self.post("/admin/login", {"email": "staff@example.invalid", "password": "wrong"}, client=self.app.test_client())
        self.assertEqual(response.status_code, 429)

    def test_order_pagination_keeps_totals_for_all_pages(self):
        for index in range(30):
            order = self.order(status="paid")
            order.name = f"Synthetic {index:02d}"
        db.session.commit()
        self.post("/admin/login", {"password": "isolated-admin"})
        response = self.client.get("/admin/orders")
        self.assertEqual(response.status_code, 200)
        self.assertIn("30 encomendas", response.get_data(as_text=True))
        self.assertNotIn("Synthetic 00", response.get_data(as_text=True))
        self.assertIn("Synthetic 00", self.client.get("/admin/orders?page=2").get_data(as_text=True))

    def test_async_payment_retains_reservation_then_failure_releases_it(self):
        order = self.order()
        self.assertEqual(self.webhook(payment_status="unpaid").status_code, 200)
        self.assertEqual(db.session.get(Order, order.id).status, "processing")
        self.assertEqual(self.webhook(event_type="checkout.session.async_payment_failed").status_code, 200)
        self.assertEqual(db.session.get(Order, order.id).status, "cancelled")

    def test_totp_matches_rfc_test_vector(self):
        from auth import totp_counter
        # RFC 6238 SHA-1 test key; six-digit truncation of the published 94287082 vector.
        self.assertEqual(totp_counter("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "287082", timestamp=59), 1)
        self.assertIsNone(totp_counter("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "000000", timestamp=59))

    def test_staff_bootstrap_is_private_and_does_not_overwrite_accounts(self):
        from models import AdminUser
        from werkzeug.security import generate_password_hash
        environment = {"STAFF_BOOTSTRAP_EMAIL": "staff@example.invalid",
                       "STAFF_BOOTSTRAP_PASSWORD_HASH": generate_password_hash("synthetic-bootstrap-password"),
                       "STAFF_BOOTSTRAP_TOTP_SECRET": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"}
        with patch.dict(os.environ, environment):
            first = self.app.test_cli_runner().invoke(args=["staff-bootstrap"])
            second = self.app.test_cli_runner().invoke(args=["staff-bootstrap"])
        self.assertEqual(first.exit_code, 0)
        self.assertEqual(second.exit_code, 0)
        self.assertEqual(AdminUser.query.count(), 1)
        self.assertNotIn(environment["STAFF_BOOTSTRAP_TOTP_SECRET"], first.output)
        self.assertNotIn(environment["STAFF_BOOTSTRAP_PASSWORD_HASH"], first.output)

    def test_concurrent_webhooks_issue_each_ticket_once(self):
        if db.engine.dialect.name != "postgresql":
            self.skipTest("Row-lock concurrency runs in the PostgreSQL CI job")
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.order()
        barrier = Barrier(2)
        def pay():
            barrier.wait(timeout=10)
            return self.webhook(client=self.app.test_client()).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: pay(), range(2)))
        self.assertEqual(results, [200, 200])
        self.assertEqual(Ticket.query.count(), 1)

    def test_concurrent_checkouts_cannot_reserve_the_last_unit_twice(self):
        if db.engine.dialect.name != "postgresql":
            self.skipTest("Row-lock concurrency runs in the PostgreSQL CI job")
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from batches import TICKET_BATCHES
        barrier = Barrier(2)
        self.app.config["PAYMENTS_LIVE"] = True
        with patch.dict(TICKET_BATCHES, {"test-entry": [{"limit": 1, "until": None, "price_cents": 1000}]}):
            clients = [self.app.test_client(), self.app.test_client()]
            for client in clients:
                self.post("/cart/add", {"product_id": 1}, client=client)
            def checkout(client):
                barrier.wait(timeout=10)
                return self.post("/checkout", {"name": "Test", "email": "buyer@example.invalid", "country": "Portugal", "ticket_name__1__0": "Test"}, client=client).status_code
            with patch.object(shop, "_start_stripe_checkout", return_value=("synthetic-stripe", 200)):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(checkout, clients))
            self.assertEqual(sorted(results), [200, 409])
            self.assertEqual(Order.query.count(), 1)

    def test_real_ticket_email_payload_is_deterministic_between_retries(self):
        from emailing import _ticket_email_payload
        from ticketing import generate_tickets_for_order
        order = self.order(status="paid")
        db.session.get(Product, 1).slug = "bilhete-geral"
        generate_tickets_for_order(order)
        db.session.commit()
        group = (order.email, order.name, list(order.tickets))
        with patch("PIL.PdfImagePlugin.time.gmtime", return_value=time.struct_time((2020, 1, 1, 0, 0, 0, 2, 1, 0))):
            first = _ticket_email_payload(order, self.app.config, *group)
        with patch("PIL.PdfImagePlugin.time.gmtime", return_value=time.struct_time((2021, 1, 1, 0, 0, 0, 4, 1, 0))):
            second = _ticket_email_payload(order, self.app.config, *group)
        self.assertEqual(first, second)

    def test_no_change_edit_keeps_qr_and_does_not_enqueue_an_email(self):
        from ticketing import generate_tickets_for_order
        order = self.order(status="paid")
        generate_tickets_for_order(order)
        db.session.commit()
        ticket = Ticket.query.one()
        ticket.holder_name = "Holder"
        db.session.commit()
        code = ticket.code
        self.post("/admin/login", {"password": "isolated-admin"})
        response = self.post(f"/admin/orders/{order.id}/edit-name", {
            "new_name": order.name, "new_email": order.email,
            f"ticket_name_{ticket.id}": "Holder", f"ticket_email_{ticket.id}": ""})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(Ticket, ticket.id).code, code)
        self.assertEqual(EmailDelivery.query.count(), 0)

    def test_email_queue_requires_admin(self):
        self.assertEqual(self.client.get("/admin/email-queue").status_code, 302)
        self.post("/admin/login", {"password": "isolated-admin"})
        self.assertEqual(self.client.get("/admin/email-queue").status_code, 200)

    def test_additive_migration_preserves_existing_order(self):
        from sqlalchemy import text, inspect
        from db_migrate import run
        order = self.order(status="paid")
        identity = order.id
        db.session.remove()
        with db.engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_orders_reservation_expires_at"))
            connection.execute(text("ALTER TABLE orders DROP COLUMN reservation_expires_at"))
        run(self.app)
        self.assertIn("reservation_expires_at", {c["name"] for c in inspect(db.engine).get_columns("orders")})
        self.assertEqual(db.session.get(Order, identity).name, "Synthetic Buyer")
