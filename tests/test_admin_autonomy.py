"""Synthetic end-to-end regressions for staff enrolment, reports and publishing."""

import base64
from datetime import datetime, timedelta
import hashlib
import hmac
import io
import re
import struct
import time
import unittest
from unittest.mock import patch, Mock

import test_purchase_security as base
from extensions import db
from models import (
    AdminUser,
    AdminInvite,
    AdminEnrollment,
    GalleryAlbum,
    GalleryPhoto,
    PageBlock,
    PageAppearance,
    Ticket,
    Product,
    OrderItem,
    NewsletterSubscriber,
    WebsiteJob,
    TicketTransfer,
    Setting,
)
from werkzeug.security import generate_password_hash
from openpyxl import load_workbook


def code(secret):
    counter = int(time.time()) // 30
    digest = hmac.new(
        base64.b32decode(secret), struct.pack(">Q", counter), hashlib.sha1
    ).digest()
    offset = digest[-1] & 15
    return f"{(struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000:06d}"


class AdminAutonomyTests(unittest.TestCase):
    def setUp(self):
        base.PurchaseSecurityTests.setUp(self)
        self.saved_config = dict(self.app.config)

    def tearDown(self):
        self.app.config.clear()
        self.app.config.update(self.saved_config)
        base.PurchaseSecurityTests.tearDown(self)

    post = base.PurchaseSecurityTests.post
    order = base.PurchaseSecurityTests.order

    def login(self, client=None, role="admin"):
        client = client or self.client
        user = AdminUser(
            email=role + "@example.invalid",
            role=role,
            password_hash=generate_password_hash("synthetic-staff-password"),
        )
        db.session.add(user)
        db.session.commit()
        with client.session_transaction() as s:
            s.update(
                is_admin=True,
                user_id=user.id,
                session_version=user.session_version,
                admin_role=role,
                admin_name=user.email,
            )
        return user

    def invite(self, email=""):
        self.login()
        response = self.post("/admin/team", {"email": email})
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r"/admin/join/([A-Za-z0-9_-]{43})", response.get_data(as_text=True)
        )
        self.assertIsNotNone(match)
        token = match[1]
        self.assertNotEqual(AdminInvite.query.one().token_hash, token)
        return token

    def test_invite_requires_verified_mfa_and_is_single_use(self):
        token = self.invite("invitee@example.invalid")
        guest = self.app.test_client()
        self.assertEqual(guest.get("/admin/join/" + token).status_code, 200)
        response = self.post(
            "/admin/join/" + token,
            {
                "email": "invitee@example.invalid",
                "password": "new-test-password-123",
                "password_confirm": "new-test-password-123",
            },
            guest,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AdminUser.query.count(), 1)
        enrollment = AdminEnrollment.query.one()
        self.assertEqual(guest.get("/admin/join/" + token + "/qr").status_code, 200)
        self.assertEqual(
            self.app.test_client().get("/admin/join/" + token + "/qr").status_code, 404
        )
        response = self.post(
            "/admin/join/" + token, {"step": "verify", "totp": "invalid"}, guest
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AdminUser.query.count(), 1)
        response = self.post(
            "/admin/join/" + token,
            {"step": "verify", "totp": code(enrollment.totp_secret)},
            guest,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AdminInvite.query.one().used_at)
        self.assertEqual(AdminUser.query.count(), 2)
        self.assertEqual(guest.get("/admin/dashboard").status_code, 200)
        self.assertEqual(guest.get("/admin/join/" + token).status_code, 410)
        self.assertEqual(AdminEnrollment.query.count(), 0)

    def test_expired_revoked_and_wrong_email_invites(self):
        token = self.invite("invitee@example.invalid")
        guest = self.app.test_client()
        self.post(
            "/admin/join/" + token,
            {
                "email": "someone@example.invalid",
                "password": "new-test-password-123",
                "password_confirm": "new-test-password-123",
            },
            guest,
        )
        self.assertEqual(AdminEnrollment.query.count(), 0)
        invite = AdminInvite.query.one()
        invite.revoked = True
        db.session.commit()
        self.assertEqual(guest.get("/admin/join/" + token).status_code, 410)
        invite.revoked = False
        invite.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        self.assertEqual(guest.get("/admin/join/" + token).status_code, 410)

    def test_revoking_issuer_invalidates_outstanding_invites(self):
        token = self.invite()
        user = AdminUser.query.one()
        user.active = False
        db.session.commit()
        self.assertEqual(
            self.app.test_client().get("/admin/join/" + token).status_code, 410
        )

    def test_checkin_account_cannot_invite_or_publish(self):
        self.login(role="checkin")
        for path in (
            "/admin/team",
            "/admin/content",
            "/admin/gallery",
            "/admin/appearance",
            "/admin/sales",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/checkin", response.location)
        self.assertEqual(AdminInvite.query.count(), 0)

    def test_dashboard_shell_and_all_admin_routes_render(self):
        self.login()
        for path in (
            "/admin/dashboard",
            "/admin/sales",
            "/admin/orders",
            "/admin/team",
            "/admin/content",
            "/admin/gallery",
            "/admin/appearance",
            "/admin/email-queue",
            "/admin/issue-ticket",
            "/admin/newsletter",
            "/admin/transfers",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(b"app-bottom-nav", response.data, path)
            self.assertIn(b"no-store", response.headers["Cache-Control"].encode())

    def test_sales_export_counts_units_and_unifies_translated_names(self):
        self.login()
        order = self.order("paid", quantity=4)
        from ticketing import generate_tickets_for_order

        generate_tickets_for_order(order)
        order.created_at = datetime(2026, 11, 3, 12)
        for i, ticket in enumerate(order.tickets):
            ticket.product_name = "Geral ticket" if i % 2 else "Bilhete Geral"
        # A second paid order outside the period must not leak into counts.
        outside = self.order("paid")
        outside.created_at = datetime(2026, 10, 2, 12)
        generate_tickets_for_order(outside)
        db.session.commit()
        response = self.client.get(
            "/admin/sales/export?start=2026-11-03&end=2026-11-03&type=test-entry"
        )
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(io.BytesIO(response.data))
        self.assertEqual(workbook["Resumo por tipo"].max_row, 2)
        self.assertEqual(workbook["Resumo por tipo"]["C2"].value, 4)
        details = list(workbook["Bilhetes individuais"].values)
        self.assertEqual(len(details), 5)
        self.assertEqual([r[6] for r in details[1:]], [1, 1, 1, 1])
        self.assertEqual(len({r[4] for r in details[1:]}), 1)
        self.assertEqual(self.client.get("/admin/sales?start=invalid").status_code, 400)
        self.assertEqual(
            self.client.get("/admin/sales?start=2026-11-04&end=2026-11-03").status_code,
            400,
        )

    def test_content_is_draft_until_published_and_html_is_escaped(self):
        self.login()
        data = {
            "page": "experience",
            "title": "<script>alert(1)</script>",
            "text": "Synthetic content",
            "sort_order": "1",
        }
        self.assertEqual(self.post("/admin/content/edit", data).status_code, 302)
        self.assertNotIn(b"Synthetic content", self.client.get("/the-experience").data)
        data.update(id=str(PageBlock.query.one().id), published="on")
        self.assertEqual(self.post("/admin/content/edit", data).status_code, 302)
        response = self.client.get("/the-experience")
        self.assertIn(b"Synthetic content", response.data)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertNotIn(b"<script>alert(1)</script>", response.data)

    def test_background_color_removes_video_and_rejects_css_payloads(self):
        self.login()
        data = {
            "page": "experience",
            "mode": "color",
            "color_start": "#123456",
            "color_end": "#abcdef",
            "layout": "list",
        }
        self.assertEqual(self.post("/admin/appearance", data).status_code, 302)
        response = self.client.get("/the-experience")
        self.assertNotIn(b"<video", response.data)
        self.assertIn(b"#123456", response.data)
        data["color_start"] = "red; background:url(https://evil.invalid)"
        self.assertEqual(self.post("/admin/appearance", data).status_code, 400)
        self.assertEqual(
            db.session.get(PageAppearance, "experience").color_start, "#123456"
        )

    def test_gallery_draft_is_private_and_published_album_has_polaroids(self):
        self.login()
        album = GalleryAlbum(
            title="Festival memories", cover_url="/static/img/experience-areas.jpg"
        )
        db.session.add(album)
        db.session.flush()
        db.session.add(
            GalleryPhoto(
                album_id=album.id,
                image_url="/static/img/experience-areas.jpg",
                caption="River",
            )
        )
        db.session.commit()
        self.assertEqual(self.client.get("/gallery/" + str(album.id)).status_code, 404)
        self.assertNotIn(b"Festival memories", self.client.get("/gallery").data)
        album.published = True
        db.session.commit()
        self.assertIn(b"Festival memories", self.client.get("/gallery").data)
        response = self.client.get("/gallery/" + str(album.id))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"data-gallery-image", response.data)

    def test_content_rejects_executable_links_and_external_media(self):
        self.login()
        for value in (
            "javascript:alert(1)",
            "//evil.invalid",
            "https://evil.invalid/track.jpg",
        ):
            self.assertEqual(
                self.post(
                    "/admin/content/edit",
                    {"page": "experience", "title": "Test", "image_url": value},
                ).status_code,
                400,
            )
        self.assertEqual(PageBlock.query.count(), 0)

    def test_newsletter_requires_consent_and_confirmation_before_sync(self):
        import json

        self.assertEqual(
            self.post(
                "/newsletter", {"email": "subscriber@example.invalid"}
            ).status_code,
            400,
        )
        self.assertEqual(NewsletterSubscriber.query.count(), 0)
        self.assertEqual(
            self.post(
                "/newsletter", {"email": "subscriber@example.invalid", "consent": "yes"}
            ).status_code,
            302,
        )
        subscriber = NewsletterSubscriber.query.one()
        self.assertIsNone(subscriber.confirmed_at)
        job = WebsiteJob.query.one()
        payload = json.loads(job.payload)
        link = re.search(r"https?://\S+", payload["text"]).group(0)
        from urllib.parse import urlsplit, parse_qs

        token = parse_qs(urlsplit(link).query)["token"][0]
        self.assertEqual(
            self.client.get("/newsletter/confirm?token=" + token).status_code, 200
        )
        self.assertIsNone(subscriber.confirmed_at)
        self.assertEqual(
            self.post("/newsletter/confirm", {"token": token}).status_code, 200
        )
        self.assertIsNotNone(subscriber.confirmed_at)
        self.assertEqual(WebsiteJob.query.filter_by(kind="newsletter_sync").count(), 1)
        self.post("/newsletter/confirm", {"token": token})
        self.assertEqual(WebsiteJob.query.filter_by(kind="newsletter_sync").count(), 1)
        from newsletter import serializer

        unsubscribe = serializer().dumps(
            {"id": subscriber.id, "nonce": subscriber.nonce, "action": "unsubscribe"}
        )
        self.assertEqual(
            self.post("/newsletter/unsubscribe", {"token": unsubscribe}).status_code,
            200,
        )
        self.assertIsNotNone(subscriber.unsubscribed_at)
        self.assertEqual(
            self.client.get("/newsletter/confirm?token=invalid").status_code, 410
        )

    def test_newsletter_repeated_requests_do_not_spam(self):
        for _ in range(3):
            self.assertEqual(
                self.post(
                    "/newsletter",
                    {"email": "subscriber@example.invalid", "consent": "yes"},
                ).status_code,
                302,
            )
        self.assertEqual(WebsiteJob.query.filter_by(kind="email").count(), 1)

    def prepare_transfer(self):
        import json
        from urllib.parse import urlsplit, parse_qs
        from ticketing import generate_tickets_for_order

        db.session.add(Setting(key="transfer_enabled", value="true"))
        order = self.order("paid")
        generate_tickets_for_order(order)
        db.session.commit()
        ticket = Ticket.query.one()
        original = ticket.code
        response = self.post(
            "/ticket-transfer", {"code": original, "email": order.email}
        )
        self.assertEqual(response.status_code, 302)
        payload = json.loads(WebsiteJob.query.filter_by(kind="email").one().payload)
        link = re.search(r"https?://\S+", payload["text"]).group(0)
        token = parse_qs(urlsplit(link).query)["token"][0]
        with patch("stripe.checkout.Session.create") as create:
            create.return_value = type(
                "Checkout",
                (),
                {"id": "cs_transfer", "url": "https://checkout.stripe.com/synthetic"},
            )()
            response = self.post(
                "/ticket-transfer/accept",
                {
                    "token": token,
                    "name": "New Holder",
                    "email": "new-holder@example.invalid",
                    "accept_terms": "yes",
                },
            )
            self.assertEqual(response.status_code, 303)
            self.assertEqual(
                create.call_args.kwargs["line_items"][0]["price_data"]["unit_amount"],
                500,
            )
        self.assertEqual(ticket.code, original)
        return ticket, TicketTransfer.query.one(), token, original

    def pay_transfer(self, transfer, **changes):
        values = dict(
            id="cs_transfer",
            amount_total=500,
            metadata={"ticket_transfer_id": transfer.id},
            payment_intent="pi_transfer",
        )
        values.update(changes)
        return base.PurchaseSecurityTests.webhook(self, **values)

    def test_paid_transfer_changes_only_after_signed_payment_and_is_idempotent(self):
        ticket, transfer, token, old_code = self.prepare_transfer()
        self.assertEqual(self.pay_transfer(transfer).status_code, 200)
        self.assertEqual(transfer.status, "completed")
        self.assertNotEqual(ticket.code, old_code)
        self.assertEqual(ticket.holder_name, "New Holder")
        self.assertEqual(ticket.holder_email, "new-holder@example.invalid")
        self.assertEqual(Ticket.query.count(), 1)
        new_code = ticket.code
        self.assertEqual(self.pay_transfer(transfer).status_code, 200)
        self.assertEqual(ticket.code, new_code)
        self.assertEqual(ticket.order.status, "paid")
        self.assertEqual(ticket.order.total_cents, 1000)

    def test_used_ticket_is_not_transferred_and_fee_refund_is_queued(self):
        ticket, transfer, token, old_code = self.prepare_transfer()
        ticket.checked_in_at = datetime.utcnow()
        db.session.commit()
        self.assertEqual(self.pay_transfer(transfer).status_code, 200)
        self.assertEqual(transfer.status, "refund_pending")
        self.assertEqual(ticket.code, old_code)
        self.assertEqual(WebsiteJob.query.filter_by(kind="transfer_refund").count(), 1)

    def test_transfer_wrong_email_is_not_enumerable(self):
        from ticketing import generate_tickets_for_order

        db.session.add(Setting(key="transfer_enabled", value="true"))
        order = self.order("paid")
        generate_tickets_for_order(order)
        db.session.commit()
        response = self.post(
            "/ticket-transfer",
            {"code": Ticket.query.one().code, "email": "wrong@example.invalid"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TicketTransfer.query.count(), 0)
        self.assertEqual(WebsiteJob.query.count(), 0)

    def test_transfer_rejects_mismatched_amount_and_preserves_original_qr(self):
        ticket, transfer, token, old_code = self.prepare_transfer()
        self.assertEqual(self.pay_transfer(transfer, amount_total=1).status_code, 400)
        self.assertEqual(ticket.code, old_code)
        self.assertEqual(transfer.status, "pending_payment")
        self.assertEqual(
            self.pay_transfer(transfer, payment_status="unpaid").status_code, 200
        )
        self.assertEqual(ticket.code, old_code)

    def test_refund_worker_refunds_only_service_fee_with_idempotency(self):
        import json
        from website_jobs import process_website_jobs

        ticket, transfer, token, old_code = self.prepare_transfer()
        ticket.checked_in_at = datetime.utcnow()
        db.session.commit()
        self.pay_transfer(transfer)
        for job in WebsiteJob.query.filter_by(kind="email"):
            job.status = "sent"
            job.payload = "{}"
        db.session.commit()
        self.app.config["EMAILS_LIVE"] = True
        with patch("stripe.Refund.create") as refund:
            process_website_jobs(self.app)
            process_website_jobs(self.app)
            refund.assert_called_once_with(
                payment_intent="pi_transfer",
                amount=500,
                idempotency_key="transfer-refund-" + transfer.id,
            )
        db.session.expire_all()
        self.assertEqual(db.session.get(TicketTransfer, transfer.id).status, "refunded")
        self.assertEqual(ticket.order.status, "paid")

    def test_newsletter_sync_respects_provider_opt_out(self):
        import json
        from website_jobs import enqueue, process_website_jobs

        subscriber = NewsletterSubscriber(
            email="subscriber@example.invalid",
            nonce="synthetic",
            consent_text="Test",
            confirmed_at=datetime.utcnow(),
        )
        db.session.add(subscriber)
        db.session.flush()
        enqueue("newsletter_sync", {"subscriber_id": subscriber.id}, "synthetic-sync")
        db.session.commit()
        self.app.config.update(
            EMAILS_LIVE=True, RESEND_NEWSLETTER_SEGMENT_ID="synthetic-segment"
        )
        with patch(
            "requests.get",
            return_value=Mock(
                status_code=200,
                json=lambda: {"id": "contact-test", "unsubscribed": True},
            ),
        ), patch("requests.post") as post, patch("requests.patch") as update:
            process_website_jobs(self.app)
            post.assert_not_called()
            update.assert_not_called()
        db.session.expire_all()
        self.assertIsNotNone(
            db.session.get(NewsletterSubscriber, subscriber.id).unsubscribed_at
        )

    def test_confirmation_email_job_retries_without_changing_idempotency_key(self):
        from website_jobs import enqueue, process_website_jobs

        enqueue(
            "email",
            {
                "from": "sender@example.invalid",
                "to": ["subscriber@example.invalid"],
                "subject": "Synthetic",
                "text": "Synthetic body",
            },
            "synthetic-mail",
        )
        db.session.commit()
        self.app.config["EMAILS_LIVE"] = True
        headers = []

        def send(*a, **kw):
            headers.append(dict(kw["headers"]))
            return Mock(status_code=503 if len(headers) == 1 else 200)

        with patch("requests.post", side_effect=send):
            process_website_jobs(self.app)
            job = WebsiteJob.query.one()
            self.assertEqual(job.status, "pending")
            job.available_at = datetime.utcnow() - timedelta(seconds=1)
            db.session.commit()
            process_website_jobs(self.app)
            process_website_jobs(self.app)
        db.session.expire_all()
        job = WebsiteJob.query.one()
        self.assertEqual(job.status, "sent")
        self.assertEqual(job.payload, "{}")
        self.assertEqual(len(headers), 2)
        self.assertEqual(headers[0]["Idempotency-Key"], headers[1]["Idempotency-Key"])

    def test_image_upload_validates_and_reencodes_before_storage(self):
        from PIL import Image
        import os

        image = io.BytesIO()
        Image.new("RGB", (16, 16), "red").save(image, format="PNG")
        self.login()
        with patch.dict(
            os.environ,
            {
                "BUNNY_STORAGE_ZONE": "synthetic-zone",
                "BUNNY_STORAGE_PASSWORD": "synthetic-storage-password",
            },
        ), patch("requests.put", return_value=Mock(status_code=201)) as upload:
            self.app.config["CDN_BASE_URL"] = "https://synthetic.b-cdn.net"
            response = self.post(
                "/admin/content/edit",
                {
                    "page": "experience",
                    "title": "Photo",
                    "image": (io.BytesIO(image.getvalue()), "original.png"),
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertTrue(upload.call_args.kwargs["data"].startswith(b"RIFF"))
            self.assertIn("/media/", PageBlock.query.one().image_url)
            response = self.post(
                "/admin/content/edit",
                {
                    "page": "experience",
                    "title": "SVG",
                    "image": (
                        io.BytesIO(b"<svg><script>alert(1)</script></svg>"),
                        "bad.svg",
                    ),
                },
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(upload.call_count, 1)

    def test_concurrent_invite_acceptance_creates_only_one_account(self):
        if db.engine.dialect.name != "postgresql":
            self.skipTest("Invitation row locking is verified in PostgreSQL CI")
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        token = self.invite()
        guests = [self.app.test_client(), self.app.test_client()]
        codes = []
        for i, guest in enumerate(guests):
            self.post(
                "/admin/join/" + token,
                {
                    "email": f"guest{i}@example.invalid",
                    "password": "synthetic-password-123",
                    "password_confirm": "synthetic-password-123",
                },
                guest,
            )
            with guest.session_transaction() as sess:
                enrollment = db.session.get(
                    AdminEnrollment,
                    sess["enrollment_" + str(AdminInvite.query.one().id)],
                )
                codes.append(code(enrollment.totp_secret))
        db.session.commit()
        barrier = Barrier(2)

        def accept(i):
            barrier.wait(timeout=10)
            return self.post(
                "/admin/join/" + token, {"step": "verify", "totp": codes[i]}, guests[i]
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(accept, range(2)))
        self.assertEqual(sorted(results), [302, 410])
        db.session.expire_all()
        self.assertEqual(AdminUser.query.count(), 2)
