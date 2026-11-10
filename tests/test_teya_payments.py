"""Synthetic-only tests: Teya signatures, totals, switching and uncertain payments."""
import base64
import json
import uuid
from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock, patch
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from werkzeug.exceptions import ServiceUnavailable
import test_admin_autonomy as autonomy
import test_purchase_security as base
from extensions import db
from models import Order, Ticket, PaymentCheckout, PaymentGatewayConfiguration, AuditEvent
import teya_gateway as teya


class TeyaPaymentTests(unittest.TestCase):
    setUp = autonomy.AdminAutonomyTests.setUp
    tearDown = autonomy.AdminAutonomyTests.tearDown
    post = base.PurchaseSecurityTests.post
    order = base.PurchaseSecurityTests.order
    login = autonomy.AdminAutonomyTests.login
    store = 'f7b056e2-13b2-477f-bf40-131cdfd0bcc5'

    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.public_key = cls.private_key.public_key().public_bytes(
            serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def config(self):
        self.app.config.update(TEYA_ENVIRONMENT='staging',TEYA_CLIENT_ID='synthetic-client',
            TEYA_CLIENT_SECRET='synthetic-secret',TEYA_STORE_ID=self.store,
            TEYA_WEBHOOK_PUBLIC_KEY=self.public_key,TEYA_CHECKOUT_READY=True)

    def payment(self):
        self.config()
        order=self.order('processing')
        order.stripe_session_id=''
        order.reservation_expires_at=None
        payment=PaymentCheckout(id=str(uuid.uuid4()),order_id=order.id,
            environment='staging',store_id=self.store,reference='synthetic-reference',
            session_id=str(uuid.uuid4()),status='active')
        db.session.add(payment);db.session.commit()
        return order,payment

    def success(self,payment,**changes):
        data={'session_id':payment.session_id,'merchant_reference':payment.reference,
              'amount':{'value':1000,'currency':'EUR'},'type':'SALE',
              'session_status':'COMPLETED','payment_status':'SUCCESS',
              'transaction_id':'tr_synthetic-transaction'}
        data.update(changes)
        return data

    def webhook(self,payment,data=None,signature=True):
        values=data or {'session_id':payment.session_id,'merchant_reference':payment.reference,
                       'amount':{'value':1000,'currency':'EUR'},'status':'SUCCESS',
                       'transaction_id':'tr_synthetic-transaction'}
        raw=json.dumps({'event':'payment.succeeded.v1','timestamp':'2026-11-10T10:00:00Z','data':values}).encode()
        signed=base64.b64encode(self.private_key.sign(raw,padding.PKCS1v15(),hashes.SHA256())).decode()
        return self.client.post('/webhooks/teya',data=raw,content_type='application/json',
                                headers={'x-teya-signature':signed if signature else 'invalid'})

    def test_signed_webhook_is_idempotent_after_switch_back_to_stripe(self):
        order,payment=self.payment()
        self.assertEqual(teya.provider(),'stripe')
        with patch.object(teya,'api',return_value=self.success(payment)):
            self.assertEqual(self.webhook(payment).status_code,200)
            self.assertEqual(self.webhook(payment).status_code,200)
        db.session.refresh(order)
        self.assertEqual(order.status,'paid')
        self.assertEqual(Ticket.query.count(),1)
        self.assertEqual(payment.transaction_id,'tr_synthetic-transaction')

    def test_invalid_missing_signature_or_wrong_key_never_calls_api(self):
        order,payment=self.payment()
        with patch.object(teya,'api') as api:
            self.assertEqual(self.webhook(payment,signature=False).status_code,400)
            self.assertEqual(self.client.post('/webhooks/teya',data=b'{}').status_code,400)
            self.app.config['TEYA_WEBHOOK_PUBLIC_KEY']=''
            self.assertEqual(self.webhook(payment).status_code,503)
            api.assert_not_called()
        self.assertEqual(Ticket.query.count(),0)

    def test_amount_currency_reference_type_and_environment_mismatches_fail_closed(self):
        order,payment=self.payment()
        cases=[{'amount':{'value':100,'currency':'EUR'}},{'amount':{'value':1000,'currency':'USD'}},
               {'merchant_reference':'other-order'},{'type':'PRE_AUTHORISATION'},
               {'session_id':str(uuid.uuid4())},{'transaction_id':''}]
        for changes in cases:
            with patch.object(teya,'api',return_value=self.success(payment,**changes)):
                self.assertEqual(self.webhook(payment).status_code,400)
            db.session.expire_all()
            self.assertEqual(db.session.get(Order,order.id).status,'processing')
            self.assertEqual(Ticket.query.count(),0)
        self.app.config['TEYA_ENVIRONMENT']='production'
        with patch.object(teya,'api') as api:
            self.assertEqual(self.webhook(payment).status_code,503)
            api.assert_not_called()

    def test_pending_never_issues_and_terminal_failure_releases_stock(self):
        order,payment=self.payment()
        with patch.object(teya,'api',return_value=self.success(payment,session_status='PROCESSING',payment_status='NONE')):
            teya.reconcile(payment.id)
        self.assertEqual(order.status,'processing')
        self.assertIsNone(order.reservation_expires_at)
        self.assertEqual(Ticket.query.count(),0)
        with patch.object(teya,'api',return_value=self.success(payment,session_status='EXPIRED',payment_status='NONE')):
            teya.reconcile(payment.id)
        self.assertEqual(order.status,'cancelled')
        self.assertEqual(payment.status,'expired')

    def test_forged_browser_success_does_not_issue(self):
        order,payment=self.payment()
        token=base.shop._order_token(order.id)
        with patch.object(teya,'api',return_value=self.success(payment,session_status='ACTIVE',payment_status='NONE')):
            r=self.client.get(f'/checkout/success/{order.id}?token={token}&payment_status=SUCCESS')
        self.assertEqual(r.status_code,200)
        self.assertEqual(Ticket.query.count(),0)
        self.assertEqual(order.status,'processing')

    def test_unavailable_lookup_is_retriable_and_cannot_issue(self):
        order,payment=self.payment()
        with patch.object(teya,'api',side_effect=teya.TeyaUnavailable('temporary')):
            self.assertEqual(self.webhook(payment).status_code,503)
        self.assertEqual(Ticket.query.count(),0)
        self.assertEqual(order.status,'processing')

    def test_start_uses_snapshot_total_and_validates_redirect(self):
        self.config()
        order=self.order()
        session_id=str(uuid.uuid4())
        with self.app.test_request_context('/checkout'):
            with patch.object(teya,'api',return_value={'session_id':session_id,'session_url':'https://checkout.teya.xyz/pay/'+session_id}) as api:
                response=teya.start_checkout(order)
        self.assertEqual(response.status_code,303)
        call=api.call_args.args
        payload=call[2]
        self.assertEqual(payload['amount'],{'value':order.total_cents,'currency':'EUR'})
        self.assertFalse(payload['save_payment_method'])
        self.assertNotIn('customer',payload)
        payment=PaymentCheckout.query.one()
        self.assertEqual(call[3],payment.id)
        self.assertEqual(order.status,'processing')
        self.assertEqual(Ticket.query.count(),0)
        payment.session_id=None
        payment.status='preparing'
        db.session.commit()
        with self.app.test_request_context('/checkout'):
            with patch.object(teya,'api',return_value={'session_id':session_id,'session_url':'https://attacker.invalid/pay'}):
                with self.assertRaises(teya.TeyaUnavailable):teya.create_session(payment)

    def test_creation_timeout_keeps_stock_and_reuses_idempotency_key(self):
        self.config()
        order=self.order()
        with self.app.test_request_context('/checkout'):
            with patch.object(teya,'api',side_effect=teya.TeyaUnavailable('timeout')):
                with self.assertRaises(ServiceUnavailable):teya.start_checkout(order)
        payment=PaymentCheckout.query.one()
        identifier=payment.id
        self.assertEqual(order.status,'processing')
        self.assertIsNone(order.reservation_expires_at)
        session_id=str(uuid.uuid4())
        with self.app.test_request_context('/checkout'):
            with patch.object(teya,'api',return_value={'session_id':session_id,'session_url':'https://checkout.teya.xyz/pay/'+session_id}) as api:
                teya.create_session(payment)
        self.assertEqual(api.call_args.args[3],identifier)
        self.assertEqual(PaymentCheckout.query.count(),1)

    def test_selector_requires_admin_csrf_confirmation_readiness_and_revision(self):
        self.assertEqual(self.client.get('/admin/payments').status_code,302)
        self.login()
        r=self.client.get('/admin/payments')
        self.assertEqual(r.status_code,200)
        self.assertEqual(PaymentGatewayConfiguration.query.count(),0)
        form={'provider':'teya','revision':'0','confirm':'yes'}
        self.assertEqual(self.client.post('/admin/payments',data=form).status_code,400)
        self.assertEqual(self.post('/admin/payments',form).status_code,409)
        self.config()
        self.assertEqual(self.post('/admin/payments',{**form,'confirm':''}).status_code,400)
        self.assertEqual(self.post('/admin/payments',form).status_code,302)
        self.assertEqual(teya.provider(),'teya')
        self.assertEqual(self.post('/admin/payments',form).status_code,409)
        self.assertEqual(self.post('/admin/payments',{'provider':'stripe','revision':'1','confirm':'yes'}).status_code,302)
        self.assertEqual(teya.provider(),'stripe')
        self.assertEqual(AuditEvent.query.filter_by(action='checkout_provider_changed').count(),2)

    def test_checkin_cannot_change_provider(self):
        self.login(role='checkin')
        self.assertEqual(self.post('/admin/payments',{'provider':'teya','revision':'0','confirm':'yes'}).status_code,302)
        self.assertEqual(PaymentGatewayConfiguration.query.count(),0)

    def test_checkout_routes_to_selected_provider_without_demo_or_stripe_fallback(self):
        self.config()
        db.session.add(PaymentGatewayConfiguration(id=1,provider='teya',revision=1));db.session.commit()
        self.assertEqual(self.post('/cart/add',{'product_id':1}).status_code,302)
        response=self.client.get('/checkout')
        self.assertIn('Pagar com Teya',response.text)
        with patch.object(teya,'start_checkout',return_value=('synthetic-teya',200)) as start:
            with patch.object(base.shop,'_start_stripe_checkout') as stripe:
                response=self.post('/checkout',{'name':'Test','email':'buyer@example.invalid','country':'Portugal','ticket_name__1__0':'Test'})
                self.assertEqual(response.status_code,200)
                self.assertEqual(start.call_count,1)
                stripe.assert_not_called()
        self.assertEqual(Ticket.query.count(),0)

    def test_live_provider_switch_requires_fresh_mfa(self):
        self.config()
        user=self.login()
        user.totp_secret='JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP'
        db.session.commit()
        self.app.config.update(PRODUCTION=True,TEYA_ENVIRONMENT='production')
        form={'provider':'teya','revision':'0','confirm':'yes'}
        self.assertEqual(self.post('/admin/payments',form).status_code,400)
        form['totp']=autonomy.code(user.totp_secret)
        self.assertEqual(self.post('/admin/payments',form).status_code,302)
        self.assertEqual(self.post('/admin/payments',{**form,'revision':'1','provider':'stripe'}).status_code,400)
        self.assertEqual(teya.provider(),'teya')

    def test_existing_stripe_webhook_still_works(self):
        self.config()
        db.session.add(PaymentGatewayConfiguration(id=1,provider='teya',revision=1))
        order=self.order()
        with patch.object(base.shop,'_check_low_stock_alerts'):
            self.assertEqual(base.PurchaseSecurityTests.webhook(self).status_code,200)
        self.assertEqual(order.status,'paid')
        self.assertEqual(Ticket.query.count(),1)

    def test_refund_recording_invalidates_tickets_without_refunding_money(self):
        order,payment=self.payment()
        with patch.object(teya,'api',return_value=self.success(payment)):
            teya.reconcile(payment.id)
        self.login()
        with patch.object(teya,'api') as api:
            self.assertEqual(self.post(f'/admin/payments/refunds/{order.id}',{'refund_reference':'refund-synthetic'}).status_code,400)
            self.assertEqual(order.status,'paid')
            self.assertEqual(self.post(f'/admin/payments/refunds/{order.id}',{'refund_reference':'refund-synthetic','confirm':'yes'}).status_code,302)
            api.assert_not_called()
        self.assertEqual(order.status,'refunded')
        self.assertEqual(AuditEvent.query.filter_by(action='teya_refund_recorded').count(),1)

    def test_background_reconciliation_works_without_request_context(self):
        self.config()
        order=self.order('processing')
        identifier=str(uuid.uuid4())
        payment=PaymentCheckout(id=identifier,order_id=order.id,environment='staging',store_id=self.store,
            reference='background-reference',status='preparing',next_check_at=datetime.utcnow()-timedelta(minutes=1))
        db.session.add(payment);db.session.commit()
        session_id=str(uuid.uuid4())
        with patch.object(teya,'api',side_effect=[{'session_id':session_id,'session_url':'https://checkout.teya.xyz/pay/'+session_id},
                {'session_id':session_id,'merchant_reference':payment.reference,'type':'SALE','amount':{'value':1000,'currency':'EUR'},
                 'session_status':'EXPIRED','payment_status':'NONE'}]) as api:
            teya.reconcile_pending(self.app)
            self.assertEqual(api.call_count,2)
        db.session.expire_all()
        self.assertEqual(db.session.get(Order,order.id).status,'cancelled')

    def test_oauth_cache_scope_rotation_and_no_redirect_following(self):
        self.config()
        teya._tokens.clear()
        response=Mock(status_code=200)
        response.json.return_value={'access_token':'synthetic-bearer','expires_in':900}
        with patch.object(teya.requests,'post',return_value=response) as post:
            self.assertEqual(teya.access_token(),'synthetic-bearer')
            self.assertEqual(teya.access_token(),'synthetic-bearer')
            self.assertEqual(post.call_count,1)
            self.assertEqual(post.call_args.kwargs['data']['scope'],teya.SCOPES)
            self.assertFalse(post.call_args.kwargs['allow_redirects'])
            self.app.config['TEYA_CLIENT_SECRET']='new-synthetic-secret'
            teya.access_token()
            self.assertEqual(post.call_count,2)

    def test_duplicate_concurrent_webhook_only_one_ticket(self):
        if db.engine.dialect.name!='postgresql':self.skipTest('PostgreSQL locks required')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        order,payment=self.payment()
        identifier=payment.id
        data=self.success(payment)
        barrier=Barrier(2)
        def confirm(_):
            with self.app.app_context():
                barrier.wait(timeout=10)
                teya.reconcile(identifier)
        with patch.object(teya,'api',return_value=data):
            with ThreadPoolExecutor(max_workers=2) as executor:list(executor.map(confirm,range(2)))
        db.session.expire_all()
        self.assertEqual(Ticket.query.count(),1)
