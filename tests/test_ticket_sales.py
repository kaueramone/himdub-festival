"""Retail configuration must affect both displayed prices and checkout constraints."""

import json
from datetime import datetime, timedelta
import unittest
from werkzeug.exceptions import Conflict
import test_admin_autonomy as autonomy
import test_purchase_security as base
from extensions import db
from models import Product, TicketConfiguration, Order, OrderItem, Ticket
from ticket_sales import (
    configured_tiers,
    configuration,
    sale_available,
    remaining_capacity,
)
from batches import current_tier


class TicketSalesTests(unittest.TestCase):
    setUp = autonomy.AdminAutonomyTests.setUp
    tearDown = autonomy.AdminAutonomyTests.tearDown
    login = autonomy.AdminAutonomyTests.login
    post = base.PurchaseSecurityTests.post

    def retail(self):
        product = Product(
            id=3,
            slug="bilhete-geral",
            category="ticket",
            name="General",
            price_cents=14000,
        )
        db.session.add(product)
        db.session.commit()
        return product

    def form(self, product):
        row = db.session.get(TicketConfiguration, product.id)
        data = {
            "name": product.name,
            "is_active": "on",
            "confirm_changes": "on",
            "revision": str(row.revision if row else 0),
        }
        tiers = configured_tiers(product.slug)
        data["tier_count"] = str(len(tiers))
        for i, tier in enumerate(tiers):
            prefix = f"tier_{i}_"
            data[prefix + "label"] = tier.get("label", f"Batch {i+1}")
            data[prefix + "price"] = str(tier["price_cents"] / 100)
            data[prefix + "limit"] = str(
                tier.get("real_limit") or tier.get("limit") or ""
            )
            if tier.get("real_limit"):
                data[prefix + "public_limit"] = str(tier.get("limit") or "")
        return data

    def test_editor_saves_prices_and_rejects_stale_changes(self):
        self.login()
        product = self.retail()
        self.assertEqual(self.client.get("/admin/ticket-sales").status_code, 200)
        self.assertEqual(self.client.get("/admin/ticket-sales/3").status_code, 200)
        data = self.form(product)
        data["tier_0_price"] = "145,50"
        self.assertEqual(self.post("/admin/ticket-sales/3", data).status_code, 302)
        self.assertEqual(product.effective_price_cents, 14550)
        self.assertEqual(configuration(product.slug)["tiers"][0]["real_limit"], 150)
        self.assertEqual(self.post("/admin/ticket-sales/3", data).status_code, 409)
        self.assertEqual(product.effective_price_cents, 14550)
        data = self.form(product)
        data["tier_0_price"] = "NaN"
        self.assertEqual(self.post("/admin/ticket-sales/3", data).status_code, 400)
        db.session.rollback()
        self.assertEqual(product.effective_price_cents, 14550)

    def test_scheduled_period_blocks_cart_without_modifying_existing_orders(self):
        self.login()
        product = self.retail()
        order = Order(
            name="Synthetic",
            email="buyer@example.invalid",
            status="pending",
            total_cents=14000,
        )
        order.items.append(
            OrderItem(
                product_id=3,
                product_name="General",
                quantity=1,
                price_cents=14000,
                batch_tier=1,
            )
        )
        db.session.add(order)
        db.session.commit()
        data = self.form(product)
        data["starts_at"] = "2030-01-01T12:00"
        data["tier_0_price"] = "150.00"
        self.assertEqual(self.post("/admin/ticket-sales/3", data).status_code, 302)
        self.assertFalse(sale_available(product.slug))
        self.assertEqual(
            self.post("/cart/add", {"product_id": "3", "quantity": "1"}).status_code,
            409,
        )
        self.assertEqual(order.total_cents, 14000)
        self.assertEqual(order.items[0].price_cents, 14000)

    def test_expired_tier_does_not_lend_unused_units_to_the_next(self):
        product = self.retail()
        tiers = [
            {
                "label": "Closed",
                "price_cents": 10000,
                "limit": 100,
                "ends_at": "2020-01-01T00:00:00",
            },
            {"label": "Current", "price_cents": 15000, "limit": 2},
        ]
        db.session.add(
            TicketConfiguration(product_id=3, payload=json.dumps({"tiers": tiers}))
        )
        db.session.commit()
        self.assertEqual(current_tier(product.slug), (2, 15000, 2))
        order = Order(
            name="Synthetic",
            email="buyer@example.invalid",
            status="paid",
            total_cents=30000,
        )
        item = OrderItem(
            product_id=3,
            product_name="General",
            quantity=2,
            price_cents=15000,
            batch_tier=2,
        )
        order.items.append(item)
        db.session.add(order)
        db.session.flush()
        for i in range(2):
            db.session.add(
                Ticket(
                    order_id=order.id,
                    order_item_id=item.id,
                    code="SYNTHETIC" + str(i),
                    product_slug=product.slug,
                    product_name=product.name,
                    batch_tier=2,
                )
            )
        db.session.commit()
        self.assertIsNone(current_tier(product.slug))

    def test_pack_and_single_ticket_share_capacity(self):
        from blueprints.shop import _validate_cart_totals

        general = self.retail()
        caravan = Product(
            id=4,
            slug="passe-caravan",
            category="ticket",
            name="Caravan",
            price_cents=8000,
        )
        bundle = Product(
            id=5,
            slug="full-pass-caravan",
            category="ticket",
            name="Pack",
            price_cents=22000,
        )
        db.session.add_all(
            [
                caravan,
                bundle,
                TicketConfiguration(
                    product_id=3,
                    payload=json.dumps(
                        {
                            "capacity": 1,
                            "tiers": [{"price_cents": 14000, "limit": None}],
                        }
                    ),
                ),
            ]
        )
        db.session.commit()
        self.assertEqual(bundle.effective_price_cents, 22000)
        self.assertEqual(remaining_capacity(general.slug), 1)
        with self.assertRaises(Conflict):
            _validate_cart_totals(
                [
                    {"product": general, "quantity": 1},
                    {"product": bundle, "quantity": 1},
                ]
            )

    def test_permission_and_csrf_protect_price_changes(self):
        product = self.retail()
        data = self.form(product)
        self.assertEqual(self.post("/admin/ticket-sales/3", data).status_code, 302)
        self.login(role="checkin")
        response = self.post("/admin/ticket-sales/3", data)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/checkin", response.location)
        self.assertIsNone(db.session.get(TicketConfiguration, 3))

    def test_concurrent_staff_updates_cannot_overwrite_each_other(self):
        if db.engine.dialect.name != "postgresql":
            self.skipTest("Product row locks require PostgreSQL")
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        self.login()
        product = self.retail()
        data = self.form(product)
        with self.client.session_transaction() as session:
            identity = dict(session)
        clients = [self.app.test_client(), self.app.test_client()]
        for client in clients:
            with client.session_transaction() as session:
                session.update(identity)
        db.session.commit()
        barrier = Barrier(2)

        def save(index):
            barrier.wait(timeout=10)
            return self.post(
                "/admin/ticket-sales/3",
                {**data, "tier_0_price": str(150 + index)},
                clients[index],
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(save, range(2)))
        self.assertEqual(sorted(statuses), [302, 409])
        db.session.expire_all()
        self.assertEqual(db.session.get(TicketConfiguration, 3).revision, 1)
