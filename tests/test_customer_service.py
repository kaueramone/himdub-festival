"""Synthetic support permissions and ticket cancellation, with no external HTTP."""
import re
import unittest
from datetime import datetime
from unittest.mock import patch
import test_admin_autonomy as autonomy
import test_purchase_security as base
from extensions import db
from models import AdminUser, AdminInvite, AdminEnrollment, AuditEvent, Ticket, Order, EmailDelivery
from ticketing import generate_tickets_for_order


class CustomerServiceTests(unittest.TestCase):
    setUp = autonomy.AdminAutonomyTests.setUp
    tearDown = autonomy.AdminAutonomyTests.tearDown
    post = base.PurchaseSecurityTests.post
    order = base.PurchaseSecurityTests.order
    login = autonomy.AdminAutonomyTests.login

    def tickets(self, quantity=2):
        order = self.order('paid',quantity)
        generate_tickets_for_order(order)
        db.session.commit()
        return order, Ticket.query.order_by(Ticket.id).all()

    def cancel(self, order, ticket, **values):
        return self.post(f'/admin/orders/{order.id}/tickets/{ticket.id}/cancel',
                         {'reason':'Pedido confirmado pelo cliente','confirm':'yes',**values})

    def test_support_only_has_three_sections_and_related_exports(self):
        self.login(role='customer_service')
        for path in ('/admin/dashboard','/admin/sales','/admin/orders','/admin/export','/admin/sales/export'):
            self.assertEqual(self.client.get(path).status_code,200,path)
        response=self.client.get('/admin/dashboard')
        for url in ('/admin/team','/admin/content','/admin/payments','/checkin/scan','/admin/issue-ticket','/admin/email-queue'):
            self.assertNotIn('href="'+url+'"',response.text)
        for path in ('/admin/team','/admin/content','/admin/payments','/admin/appearance',
                     '/admin/gallery','/admin/lineup','/admin/newsletter','/admin/transfers',
                     '/admin/ticket-sales','/admin/customer-requests','/admin/issue-ticket',
                     '/admin/email-queue','/checkin/scan','/checkin/UNKNOWN'):
            self.assertEqual(self.client.get(path).status_code,403,path)
        for path in ('/admin/team','/admin/payments','/admin/issue-ticket','/admin/activate-real-sales'):
            self.assertEqual(self.post(path).status_code,403,path)
        with self.client.session_transaction() as s:
            s['admin_role']='admin'
        self.assertEqual(self.client.get('/admin/team').status_code,403)

    def test_cancellation_invalidates_only_selected_ticket_and_preserves_payment(self):
        self.login(role='customer_service')
        order,tickets=self.tickets()
        first,second=tickets
        original_code=first.code
        self.assertEqual(self.cancel(order,first).status_code,302)
        self.assertIsNotNone(first.cancelled_at)
        self.assertEqual(first.cancelled_by,'customer_service@example.invalid')
        self.assertIsNone(second.cancelled_at)
        self.assertEqual(order.status,'paid')
        self.assertEqual(order.total_cents,2000)
        from customer_portal import downloadable
        from transfers import ticket_eligible
        from emailing import _group_tickets_by_recipient
        self.assertFalse(downloadable(first))
        self.assertFalse(ticket_eligible(first))
        self.assertEqual([t.id for g in _group_tickets_by_recipient(order) for t in g[2]],[second.id])
        generate_tickets_for_order(order)
        db.session.commit()
        self.assertEqual(Ticket.query.count(),2)
        self.cancel(order,first)
        self.assertEqual(AuditEvent.query.filter_by(action='ticket_cancelled').count(),1)
        gate=self.app.test_client()
        self.login(client=gate,role='checkin')
        self.assertEqual(gate.get('/checkin/'+original_code).status_code,403)
        self.assertEqual(self.post('/checkin/'+original_code,{'wristband_code':'cancelled-test'},gate).status_code,403)
        self.assertEqual(gate.get('/checkin/'+second.code).status_code,200)

    def test_cancellation_requires_csrf_confirmation_reason_and_matching_order(self):
        self.login(role='customer_service')
        order,tickets=self.tickets()
        ticket=tickets[0]
        path=f'/admin/orders/{order.id}/tickets/{ticket.id}/cancel'
        self.assertEqual(self.client.post(path,data={'reason':'Valid reason','confirm':'yes'}).status_code,400)
        self.assertEqual(self.cancel(order,ticket,confirm='').status_code,400)
        self.assertEqual(self.cancel(order,ticket,reason='x').status_code,400)
        other=self.order('paid')
        self.assertEqual(self.cancel(other,ticket).status_code,404)
        ticket.checked_in_at=datetime.utcnow()
        db.session.commit()
        self.assertEqual(self.cancel(order,ticket).status_code,409)
        self.assertIsNone(ticket.cancelled_at)

    def test_cancelled_tickets_cannot_be_reissued_by_edit_or_resend(self):
        self.login(role='customer_service')
        order,tickets=self.tickets(1)
        ticket=tickets[0]
        self.cancel(order,ticket)
        self.app.config['EMAILS_LIVE']=True
        r=self.post(f'/admin/orders/{order.id}/resend-ticket',{'resend_key':'synthetic-resend-key-long'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(EmailDelivery.query.count(),0)
        from outbox import enqueue_order_emails
        enqueue_order_emails(order)
        db.session.commit()
        self.assertEqual(EmailDelivery.query.count(),0)
        previous=ticket.code
        r=self.post(f'/admin/orders/{order.id}/edit-name',{'new_name':'Updated Buyer','new_email':'updated@example.invalid'})
        self.assertEqual(r.status_code,302)
        self.assertIsNotNone(ticket.cancelled_at)
        self.assertEqual(ticket.code,previous)
        self.assertEqual(EmailDelivery.query.count(),0)

    def test_support_can_edit_and_resend_valid_tickets(self):
        self.login(role='customer_service')
        order,tickets=self.tickets(1)
        ticket=tickets[0]
        old_code=ticket.code
        self.app.config['EMAILS_LIVE']=True
        r=self.post(f'/admin/orders/{order.id}/edit-name',{'new_name':'Corrected Buyer',
                    'new_email':'buyer@example.invalid',f'ticket_name_{ticket.id}':'Corrected Holder'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(ticket.holder_name,'Corrected Holder')
        self.assertNotEqual(ticket.code,old_code)
        self.assertEqual(self.post(f'/admin/orders/{order.id}/resend-ticket',{'resend_key':'synthetic-resend-key-long'}).status_code,302)
        self.assertGreater(EmailDelivery.query.count(),0)
        self.assertEqual(AuditEvent.query.filter_by(action='email-resend').count(),1)

    def test_support_invitation_keeps_stored_role_and_requires_mfa(self):
        self.login()
        response=self.post('/admin/team',{'email':'support@example.invalid','role':'customer_service'})
        self.assertEqual(response.status_code,200)
        token=re.search(r'/admin/join/([A-Za-z0-9_-]{43})',response.text)[1]
        self.assertEqual(AdminInvite.query.one().role,'customer_service')
        guest=self.app.test_client()
        self.post('/admin/join/'+token,{'email':'support@example.invalid','password':'synthetic-support-password',
                  'password_confirm':'synthetic-support-password','role':'admin'},guest)
        enrollment=AdminEnrollment.query.one()
        response=self.post('/admin/join/'+token,{'step':'verify','totp':autonomy.code(enrollment.totp_secret),'role':'admin'},guest)
        self.assertTrue(response.location.endswith('/admin/dashboard'))
        user=AdminUser.query.filter_by(email='support@example.invalid').one()
        self.assertEqual(user.role,'customer_service')
        self.assertTrue(user.totp_secret)
        self.assertEqual(guest.get('/admin/team').status_code,403)
        user.active=False
        db.session.commit()
        self.assertEqual(guest.get('/admin/orders').status_code,302)

    def test_support_without_mfa_cannot_authenticate_in_production(self):
        user=self.login(role='customer_service')
        self.app.config['PRODUCTION']=True
        from auth import authenticate
        with self.app.test_request_context('/admin/login',method='POST'):
            self.assertFalse(authenticate(user.email,'synthetic-staff-password'))

    def test_cancelled_ticket_is_reported_without_erasing_paid_revenue(self):
        self.login(role='customer_service')
        order,tickets=self.tickets()
        self.cancel(order,tickets[0])
        from admin_reports import ticket_query
        with self.app.test_request_context('/admin/sales?state=cancelled'):
            self.assertEqual(ticket_query().count(),1)
        response=self.client.get('/admin/sales?state=cancelled')
        self.assertIn('Cancelado · pagamento mantido',response.text)
        self.assertEqual(self.client.get('/admin/sales/export?state=cancelled').status_code,200)
        self.assertIn('€20,00',self.client.get('/admin/dashboard').text)

    def test_existing_ticket_schema_adds_cancellation_columns_idempotently(self):
        from sqlalchemy import text
        from db_migrate import run
        order,tickets=self.tickets(1)
        identifier=tickets[0].id
        db.session.remove()
        with db.engine.begin() as conn:
            for column in ('cancelled_at','cancelled_by','cancellation_reason'):
                conn.execute(text('ALTER TABLE tickets DROP COLUMN '+column))
        run(self.app)
        run(self.app)
        self.assertIsNone(db.session.get(Ticket,identifier).cancelled_at)

    def test_cancellation_does_not_interrupt_queued_valid_ticket_delivery(self):
        self.login(role='customer_service')
        order,tickets=self.tickets()
        self.app.config['EMAILS_LIVE']=True
        from outbox import enqueue_order_emails, _groups, _revision
        enqueue_order_emails(order)
        db.session.commit()
        self.cancel(order,tickets[0])
        group=_groups(order)[0]
        revision=_revision(order,*group)
        self.assertEqual([t.id for t in group[2]],[tickets[1].id])
        self.assertIsNotNone(EmailDelivery.query.filter_by(revision=revision).first())

    def test_cancellation_during_holder_payment_requires_admin_intervention(self):
        from models import TicketTransfer
        self.login(role='customer_service')
        order,tickets=self.tickets(1)
        ticket=tickets[0]
        db.session.add(TicketTransfer(id='synthetic-transfer',ticket_id=ticket.id,active_ticket_id=ticket.id,
            token_hash='x'*64,original_code_hash='y'*64,original_email=order.email,
            fee_cents=500,status='pending_payment',expires_at=datetime.utcnow()))
        db.session.commit()
        self.assertEqual(self.cancel(order,ticket).status_code,409)
        self.assertIsNone(ticket.cancelled_at)
