"""Customer email verification, strict ownership boundaries and support requests."""

from datetime import datetime, timedelta, timezone
import json
import re
import unittest
from unittest.mock import patch
import test_admin_autonomy as autonomy
import test_purchase_security as base
from extensions import db
from models import (
    CustomerAccount,
    CustomerAccessToken,
    CustomerRequest,
    Order,
    Ticket,
    WebsiteJob,
)
from ticketing import generate_tickets_for_order
from customer_portal import digest


class CustomerPortalTests(unittest.TestCase):
    setUp = autonomy.AdminAutonomyTests.setUp
    tearDown = autonomy.AdminAutonomyTests.tearDown
    post = base.PurchaseSecurityTests.post
    order = base.PurchaseSecurityTests.order
    staff_login = autonomy.AdminAutonomyTests.login

    def buyer_login(self, email="buyer@example.invalid", client=None):
        client = client or self.client
        response = self.post("/account/login", {"email": email}, client)
        self.assertEqual(response.status_code, 200)
        payload = json.loads(
            WebsiteJob.query.filter_by(kind="email")
            .order_by(WebsiteJob.created_at.desc())
            .first()
            .payload
        )
        token = re.search(r"/account/access/([A-Za-z0-9_-]{43})", payload["text"])[1]
        self.assertIn("logo-him-dub.png", payload["html"])
        self.assertNotEqual(
            CustomerAccessToken.query.filter_by(email=email).one().token_hash, token
        )
        self.assertEqual(client.get("/account/access/" + token).status_code, 200)
        with client.session_transaction() as session:
            self.assertNotIn("customer_id", session)
        self.assertEqual(
            self.post("/account/access/" + token, client=client).status_code, 302
        )
        return token

    def paid_ticket(self):
        order = self.order("paid")
        generate_tickets_for_order(order)
        db.session.commit()
        return order, Ticket.query.one()

    def test_access_link_requires_confirmation_and_is_single_use(self):
        token = self.buyer_login()
        self.assertEqual(self.client.get("/account/").status_code, 200)
        self.assertEqual(self.post("/account/access/" + token).status_code, 410)
        self.assertEqual(CustomerAccount.query.count(), 1)
        with self.client.session_transaction() as session:
            self.assertNotIn("is_admin", session)
        response = self.client.get("/account/profile")
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_expired_or_forged_access_link_is_rejected(self):
        raw = "A" * 43
        db.session.add(
            CustomerAccessToken(
                token_hash=digest(raw),
                email="buyer@example.invalid",
                expires_at=datetime.utcnow() - timedelta(seconds=1),
            )
        )
        db.session.commit()
        self.assertEqual(self.post("/account/access/" + raw).status_code, 410)
        self.assertEqual(
            self.client.get("/account/access/" + "B" * 43).status_code, 410
        )
        self.assertEqual(CustomerAccount.query.count(), 0)

    def test_customer_sees_only_their_orders_and_profile_is_separate(self):
        own = self.order("paid")
        other = self.order("paid")
        other.email = "other@example.invalid"
        other.name = "OTHER BUYER PRIVATE NAME"
        db.session.commit()
        self.buyer_login()
        self.assertEqual(self.client.get(f"/account/orders/{own.id}").status_code, 200)
        self.assertEqual(
            self.client.get(f"/account/orders/{other.id}").status_code, 404
        )
        self.assertNotIn(
            b"OTHER BUYER PRIVATE NAME", self.client.get("/account/orders").data
        )
        self.assertEqual(
            self.post(
                "/account/profile",
                {
                    "name": "New profile name",
                    "phone": "+351 999 111 222",
                    "email": "other@example.invalid",
                },
            ).status_code,
            302,
        )
        self.assertNotEqual(own.name, "New profile name")
        self.assertEqual(CustomerAccount.query.one().email, own.email)

    def test_transferred_holder_cannot_access_original_order_or_buyer_name(self):
        order, ticket = self.paid_ticket()
        order.name = "ORIGINAL BUYER PRIVATE"
        ticket.holder_email = "recipient@example.invalid"
        ticket.holder_name = ""
        db.session.commit()
        self.buyer_login("recipient@example.invalid")
        self.assertEqual(
            self.client.get(f"/account/orders/{order.id}").status_code, 404
        )
        self.assertNotIn(
            b"ORIGINAL BUYER PRIVATE", self.client.get("/account/tickets").data
        )
        with patch(
            "ticket_image.generate_ticket_image_bytes", return_value=b"%PDF-synthetic"
        ) as render:
            self.assertEqual(
                self.client.get(f"/account/tickets/{ticket.id}/download").status_code,
                200,
            )
            self.assertEqual(render.call_args.args[0].holder_name, "Titular do bilhete")
        other_client = self.app.test_client()
        self.buyer_login("buyer@example.invalid", other_client)
        self.assertEqual(
            other_client.get(f"/account/tickets/{ticket.id}/download").status_code, 404
        )
        self.assertNotIn(
            f"/tickets/{ticket.id}/download".encode(),
            other_client.get(f"/account/orders/{order.id}").data,
        )

    def test_cancelled_or_used_tickets_cannot_be_downloaded_or_changed(self):
        order, ticket = self.paid_ticket()
        self.buyer_login()
        for state in ("cancelled", "refunded", "pending"):
            order.status = state
            db.session.commit()
            self.assertEqual(
                self.client.get(f"/account/tickets/{ticket.id}/download").status_code,
                409,
            )
            self.assertEqual(
                self.client.get(f"/account/tickets/{ticket.id}/request").status_code,
                409,
            )
        order.status = "paid"
        ticket.checked_in_at = datetime.utcnow()
        db.session.commit()
        self.assertEqual(
            self.client.get(f"/account/tickets/{ticket.id}/download").status_code, 409
        )

    def test_valid_owned_ticket_download_uses_the_existing_pdf_renderer(self):
        order, ticket = self.paid_ticket()
        ticket.product_slug = "bilhete-geral"
        ticket.holder_name = "Synthetic Holder"
        db.session.commit()
        self.buyer_login()
        response = self.client.get(f"/account/tickets/{ticket.id}/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF"))
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_support_request_is_owned_deduplicated_and_does_not_change_qr(self):
        order, ticket = self.paid_ticket()
        original = ticket.code
        self.buyer_login()
        form = {
            "kind": "name_correction",
            "name": "Correct Name",
            "email": "buyer@example.invalid",
            "message": "Please correct my name.",
        }
        self.assertEqual(
            self.post(f"/account/tickets/{ticket.id}/request", form).status_code, 302
        )
        self.assertEqual(
            self.post(f"/account/tickets/{ticket.id}/request", form).status_code, 409
        )
        self.assertEqual(ticket.code, original)
        item = CustomerRequest.query.one()
        self.assertEqual(item.status, "pending")
        staff = self.app.test_client()
        self.staff_login(staff)
        self.assertEqual(staff.get("/admin/customer-requests").status_code, 200)
        self.assertEqual(
            self.post(
                "/admin/customer-requests",
                {
                    "id": str(item.id),
                    "status": "resolved",
                    "response": "We have reviewed your request.",
                },
                staff,
            ).status_code,
            302,
        )
        self.assertIn(
            b"We have reviewed your request.", self.client.get("/account/requests").data
        )
        self.assertEqual(ticket.code, original)
        self.assertIsNone(item.active_ticket_id)

    def test_logout_revokes_other_customer_sessions_and_expired_sessions_fail(self):
        self.buyer_login()
        second = self.app.test_client()
        with self.client.session_transaction() as session:
            identity = dict(session)
        with second.session_transaction() as session:
            session.update(identity)
        self.assertEqual(self.post("/account/logout").status_code, 302)
        self.assertEqual(second.get("/account/").status_code, 302)
        account = CustomerAccount.query.one()
        with self.client.session_transaction() as session:
            session.update(
                customer_id=account.id,
                customer_version=account.session_version,
                customer_started=1,
            )
        self.assertEqual(self.client.get("/account/").status_code, 302)

    def test_login_email_is_throttled_and_does_not_disclose_purchases(self):
        self.order("paid")
        first = self.post("/account/login", {"email": "buyer@example.invalid"})
        second = self.post("/account/login", {"email": "unknown@example.invalid"})
        self.assertEqual(first.data, second.data)
        self.post("/account/login", {"email": "buyer@example.invalid"})
        self.assertEqual(WebsiteJob.query.filter_by(kind="email").count(), 2)
        self.assertEqual(
            self.client.post(
                "/account/login", data={"email": "buyer@example.invalid"}
            ).status_code,
            400,
        )

    def test_concurrent_access_link_use_creates_only_one_session(self):
        if db.engine.dialect.name != "postgresql":
            self.skipTest("Access link row locks require PostgreSQL")
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        raw = "C" * 43
        db.session.add(
            CustomerAccessToken(
                token_hash=digest(raw),
                email="buyer@example.invalid",
                expires_at=datetime.utcnow() + timedelta(minutes=20),
            )
        )
        db.session.commit()
        clients = [self.app.test_client(), self.app.test_client()]
        barrier = Barrier(2)

        def accept(index):
            barrier.wait(timeout=10)
            return self.post(
                "/account/access/" + raw, client=clients[index]
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(accept, range(2)))
        self.assertEqual(sorted(results), [302, 410])
        db.session.expire_all()
        self.assertEqual(CustomerAccount.query.count(), 1)
