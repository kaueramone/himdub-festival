"""Opt-in collection, privacy erasure, paid attribution and truthful reports."""
from datetime import datetime, timedelta
import html
import json
import re
import unittest
from unittest.mock import patch

import test_purchase_security as base
import test_admin_autonomy as autonomy
from extensions import db
from models import AudienceConsent, AudienceVisit, AudiencePage, AudienceOrder, Order, PaymentCheckout
import audience
from audience_reports import report


class AudienceTests(unittest.TestCase):
    setUp = autonomy.AdminAutonomyTests.setUp
    tearDown = autonomy.AdminAutonomyTests.tearDown
    post = base.PurchaseSecurityTests.post
    order = base.PurchaseSecurityTests.order
    login = autonomy.AdminAutonomyTests.login

    def grant(self, client=None, path='/'):
        client = client or self.client
        with client.session_transaction() as session:
            session['csrf_token'] = 'isolated-csrf'
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        return html.unescape(re.search(r'data-audience-token="([^"]*)"', response.get_data(as_text=True))[1])

    def allow(self, client=None):
        response = self.post('/audience/consent', {'choice':'yes'}, client)
        self.assertEqual(response.status_code, 200)
        return response

    def collect(self, token, client=None, **data):
        return self.post('/audience/collect', {'data':json.dumps({'token':token, **data})}, client)

    def measured(self):
        token = self.grant()
        self.allow()
        self.assertEqual(self.collect(token).status_code, 200)
        return AudienceVisit.query.one()

    def metrics(self):
        with self.app.test_request_context('/admin/audience?days=30'):
            return report()

    def test_no_collection_before_consent_or_after_rejection(self):
        token = self.grant()
        self.assertEqual(self.collect(token).status_code, 204)
        self.assertEqual(AudienceConsent.query.count(), 0)
        self.assertIsNone(self.client.get_cookie(audience.VISITOR_COOKIE))
        self.post('/audience/consent', {'choice':'no'})
        self.assertEqual(self.collect(token).status_code, 204)
        self.assertEqual(AudienceVisit.query.count(), 0)

    def test_signed_page_dedup_and_heartbeat_bounds(self):
        visit = self.measured()
        token = self.grant(path='/tickets')
        self.assertEqual(self.collect(token, seconds=100000, scroll=900).status_code, 200)
        self.collect(token, seconds=0, scroll=-100)
        db.session.expire_all()
        self.assertEqual(visit.page_views, 2)
        self.assertEqual(AudiencePage.query.count(), 2)
        page = AudiencePage.query.filter_by(page='shop.tickets').one()
        self.assertLessEqual(page.active_seconds, 3)
        self.assertEqual(page.scroll_percent, 100)
        self.assertEqual(visit.exit_page, 'shop.tickets')
        self.assertNotIn('isolated', visit.visitor_key)

    def test_forgery_pii_payload_csrf_and_body_limit_rejected(self):
        token = self.grant()
        self.allow()
        for data in ({'token':'forged'}, {'token':token,'purchase':True},
                     {'token':token,'email':'buyer@example.invalid'}, {'token':token,'seconds':True}):
            response = self.post('/audience/collect', {'data':json.dumps(data)})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post('/audience/consent', data={'choice':'yes'}).status_code, 400)
        self.assertEqual(self.collect(token, campaign='x'*5000).status_code, 413)
        self.assertEqual(AudienceVisit.query.count(), 0)

    def test_token_cannot_be_replayed_in_another_browser_session(self):
        token = self.grant()
        other = self.app.test_client()
        with other.session_transaction() as session:
            session['csrf_token'] = 'another-csrf'
        other.post('/audience/consent', data={'csrf_token':'another-csrf','choice':'yes'})
        response = other.post('/audience/collect', data={'csrf_token':'another-csrf','data':json.dumps({'token':token})})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(AudienceVisit.query.count(), 0)

    def test_admin_bots_and_privacy_signals_excluded(self):
        for headers in ({'DNT':'1'}, {'Sec-GPC':'1'}, {'User-Agent':'Googlebot'}):
            guest = self.app.test_client()
            with guest.session_transaction() as session:
                session['csrf_token'] = 'isolated-csrf'
            result = guest.post('/audience/consent', data={'csrf_token':'isolated-csrf','choice':'yes'}, headers=headers)
            self.assertEqual(result.json['choice'], 'blocked')
        self.login()
        self.assertEqual(self.grant(), '')
        self.assertEqual(self.post('/audience/consent', {'choice':'yes'}).json['choice'], 'blocked')
        self.assertEqual(AudienceConsent.query.count(), 0)

    def test_private_customer_pages_have_no_measurement_grant(self):
        response = self.client.get('/account/login')
        self.assertNotIn('data-audience-token=', response.get_data(as_text=True))

    def test_acceptance_idempotent_and_withdrawal_preserves_finances(self):
        visit = self.measured()
        order = self.order(status='paid')
        db.session.add(AudienceOrder(order_id=order.id, visit_id=visit.id))
        db.session.commit()
        old_visitor = self.client.get_cookie(audience.VISITOR_COOKIE).value
        old_visit = self.client.get_cookie(audience.VISIT_COOKIE).value
        self.allow()
        self.assertEqual(AudienceConsent.query.count(), 1)
        self.post('/audience/consent', {'choice':'no'})
        self.assertEqual(AudienceVisit.query.count(), 0)
        self.assertEqual(AudiencePage.query.count(), 0)
        self.assertEqual(AudienceOrder.query.count(), 0)
        self.assertEqual(db.session.get(Order, order.id).status, 'paid')
        self.assertIsNotNone(AudienceConsent.query.one().revoked_at)
        # An in-flight browser with old cookies cannot recreate erased data.
        self.client.set_cookie(audience.CONSENT_COOKIE, 'yes')
        self.client.set_cookie(audience.VISITOR_COOKIE, old_visitor)
        self.client.set_cookie(audience.VISIT_COOKIE, old_visit)
        self.assertEqual(self.collect(self.grant()).status_code, 204)
        self.assertEqual(AudienceVisit.query.count(), 0)

    def test_source_device_and_country_store_only_coarse_fields(self):
        token = self.grant()
        self.allow()
        self.client.environ_base['HTTP_USER_AGENT'] = 'Mozilla/5.0 (iPhone; Mobile) Version/17 Safari/605'
        with patch('audience.country', return_value='PT'):
            self.collect(token, referrer='https://www.instagram.com/private/path?email=private@example.invalid', campaign='edicao2027')
        visit = AudienceVisit.query.one()
        self.assertEqual((visit.source,visit.medium,visit.country,visit.device,visit.browser), ('Instagram','social','PT','Telemóvel','Safari'))
        self.assertNotIn('email', str(visit.__dict__))
        with self.app.test_request_context('/'):
            self.assertEqual(audience.origin_info({'source':'instagram','medium':'email','campaign':'person123456789'})[2], '')
            self.assertEqual(audience.origin_info({'referrer':'http://8.8.8.8/private'})[1], 'direct')

    def test_successful_cart_actions_and_order_attribution(self):
        visit = self.measured()
        self.assertEqual(self.post('/cart/add', {'product_id':'1','quantity':'1'}).status_code, 302)
        db.session.refresh(visit)
        self.assertIsNotNone(visit.cart_at)
        order = self.order()
        # Run the same transaction boundary used by checkout, with its cookies.
        cookies = '; '.join(c.key+'='+c.value for c in (self.client.get_cookie(audience.CONSENT_COOKIE),self.client.get_cookie(audience.VISITOR_COOKIE),self.client.get_cookie(audience.VISIT_COOKIE)))
        with self.app.test_request_context('/', headers={'Cookie':cookies}):
            audience.link_order(order)
            db.session.commit()
        self.assertEqual(AudienceOrder.query.one().order_id, order.id)
        self.assertEqual(self.metrics()['buyers'], 0)
        order.status = 'paid'
        db.session.commit()
        self.assertEqual(self.metrics()['buyers'], 1)

    def test_attribution_error_does_not_roll_back_purchase(self):
        order = self.order()
        with self.app.test_request_context('/'):
            with patch('audience.current_visit', side_effect=RuntimeError('synthetic')):
                audience.link_order(order)
            order.status = 'paid'
            db.session.commit()
        self.assertEqual(db.session.get(Order, order.id).status, 'paid')

    def test_real_checkout_transaction_attributes_only_consented_purchase(self):
        self.measured()
        self.post('/cart/add', {'product_id':1})
        self.app.config['PAYMENTS_LIVE'] = True
        from blueprints import shop
        with patch.object(shop, '_start_stripe_checkout', return_value=('synthetic checkout',200)):
            response = self.post('/checkout', {'name':'Synthetic Buyer','email':'buyer@example.invalid',
                'country':'Portugal','ticket_name__1__0':'Synthetic Buyer'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(AudienceOrder.query.count(),1)
        self.assertIsNotNone(AudienceVisit.query.one().checkout_at)
        self.assertEqual(self.metrics()['buyers'],0)
        other = self.app.test_client()
        self.post('/cart/add', {'product_id':1}, other)
        with patch.object(shop, '_start_stripe_checkout', return_value=('synthetic checkout',200)):
            self.post('/checkout', {'name':'Another Buyer','email':'other@example.invalid',
                'country':'Portugal','ticket_name__1__0':'Another Buyer'}, other)
        self.assertEqual(Order.query.count(),2)
        self.assertEqual(AudienceOrder.query.count(),1)

    def test_no_ip_persisted_and_local_country_failure_is_safe(self):
        class Reader:
            def get(self, address):
                return {'country':{'iso_code':'PT'}}
        with patch.dict(self.app.extensions, {'country_geolocation':Reader()}):
            with self.app.test_request_context('/', environ_base={'REMOTE_ADDR':'8.8.8.8'}):
                self.assertEqual(audience.country(),'PT')
            with self.app.test_request_context('/', environ_base={'REMOTE_ADDR':'127.0.0.1'}):
                self.assertEqual(audience.country(),'')
        self.measured()
        names = set(AudienceVisit.__table__.columns.keys())
        self.assertFalse(names & {'ip','ip_address','user_agent','url','email','name'})

    def test_language_and_public_consent_copy_are_reviewed_in_four_languages(self):
        from audience_copy import COPY
        for language in ('pt','en','fr','es'):
            response = self.client.get('/' if language=='pt' else '/'+language+'/')
            self.assertEqual(response.status_code,200)
            self.assertIn(COPY[language]['title'],html.unescape(response.get_data(as_text=True)))
        self.assertEqual(self.client.get('/infos/cookies').status_code,200)

    def seed_visit(self, identifier, age=2):
        key = identifier.ljust(64,'x')
        db.session.add(AudienceConsent(key=key, expires_at=datetime.utcnow()+timedelta(days=90)))
        visit = AudienceVisit(id=identifier, visitor_key=key, entry_page='shop.tickets', exit_page='shop.checkout',
            started_at=datetime.utcnow()-timedelta(days=age), last_seen=datetime.utcnow()-timedelta(days=age),
            page_views=2, active_seconds=60, cart_at=datetime.utcnow()-timedelta(days=age), checkout_at=datetime.utcnow()-timedelta(days=age))
        db.session.add(visit)
        db.session.commit()
        return visit

    def linked_order(self, visit, status='pending', state='NONE', session_state='ACTIVE', error=''):
        order = self.order(status=status)
        db.session.add(AudienceOrder(order_id=order.id,visit_id=visit.id))
        db.session.add(PaymentCheckout(id=str(order.id),order_id=order.id,environment='production',store_id='test',reference=str(order.id),
            session_state=session_state,payment_state=state,last_error=error))
        db.session.commit()
        return order

    def test_abandonment_separate_from_failed_and_unresolved_payment(self):
        self.seed_visit('abandoned')
        self.linked_order(self.seed_visit('failed'), state='FAILED')
        self.linked_order(self.seed_visit('waiting'), session_state='PROCESSING')
        self.linked_order(self.seed_visit('unknown'), error='Provider unavailable')
        self.seed_visit('recent', age=0)
        result = self.metrics()
        self.assertEqual((result['abandoned'],result['failed'],result['uncertain']), (1,1,2))

    def test_failure_then_success_is_one_conversion_even_with_multiple_orders(self):
        visit = self.seed_visit('recovered')
        self.linked_order(visit,state='FAILED')
        self.linked_order(visit,status='paid',state='SUCCESS')
        self.linked_order(visit,status='paid',state='SUCCESS')
        self.order(status='paid')  # Operational sale without navigation consent.
        result = self.metrics()
        self.assertEqual((result['buyers'],result['orders_paid'],result['rate']), (1,2,100))
        self.assertEqual((result['failed'],result['abandoned']), (0,0))
        self.assertEqual(result['global_paid'],3)

    def test_retention_erases_only_analytics(self):
        visit = self.seed_visit('expired',age=91)
        order = self.linked_order(visit,status='paid',state='SUCCESS')
        db.session.add(AudiencePage(id='a'*64, visit_id=visit.id, page='shop.tickets'))
        AudienceConsent.query.one().expires_at = datetime.utcnow()-timedelta(days=1)
        db.session.commit()
        audience.cleanup(force=True)
        db.session.commit()
        self.assertEqual(AudienceVisit.query.count()+AudiencePage.query.count()+AudienceOrder.query.count()+AudienceConsent.query.count(),0)
        self.assertEqual(db.session.get(Order,order.id).status,'paid')

    def test_dashboard_empty_state_access_controls_and_invalid_dates(self):
        self.assertEqual(self.client.get('/admin/audience').status_code,302)
        for role in ('customer_service','checkin'):
            guest = self.app.test_client()
            self.login(guest,role=role)
            self.assertIn(guest.get('/admin/audience').status_code,(302,403))
        self.login()
        response = self.client.get('/admin/audience')
        self.assertEqual(response.status_code,200)
        self.assertIn('Público e comportamento', response.get_data(as_text=True))
        self.assertEqual(self.client.get('/admin/audience?start=2020-01-01').status_code,400)

    def test_collection_budget_is_bounded(self):
        self.measured()
        row = AudienceConsent.query.one()
        row.event_count=1000
        db.session.commit()
        self.assertEqual(self.collect(self.grant()).status_code,429)

    def test_concurrent_withdrawal_and_heartbeat_cannot_resurrect_visit(self):
        if db.engine.dialect.name != 'postgresql':
            self.skipTest('Row-lock concurrency requires PostgreSQL')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.measured()
        token = self.grant()
        jar = {name:self.client.get_cookie(name).value for name in ('session',audience.CONSENT_COOKIE,audience.VISITOR_COOKIE,audience.VISIT_COOKIE)}
        barrier = Barrier(2)
        def act(withdraw):
            client = self.app.test_client()
            for name,value in jar.items():
                client.set_cookie(name,value)
            barrier.wait(timeout=10)
            path = '/audience/consent' if withdraw else '/audience/collect'
            data = {'choice':'no'} if withdraw else {'data':json.dumps({'token':token,'seconds':5})}
            return client.post(path,data={**data,'csrf_token':'isolated-csrf'}).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(act,(True,False)))
        self.assertIn(results[0], (200,204))
        self.assertIn(results[1], (200,204))
        db.session.expire_all()
        self.assertIsNotNone(AudienceConsent.query.one().revoked_at)
        self.assertEqual(AudienceVisit.query.count(),0)
