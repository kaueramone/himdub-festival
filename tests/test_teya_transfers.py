"""No real buyers or charges: complete holder-change flow and hostile callbacks."""
import hashlib, json, re, uuid
from datetime import datetime
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
import unittest
import test_teya_payments as teya_tests
import test_admin_autonomy as autonomy
from extensions import db
from models import Ticket, TicketTransfer, TransferCheckout, WebsiteJob, EmailDelivery, Setting, PaymentGatewayConfiguration
from ticketing import generate_tickets_for_order
import teya_gateway as gateway
import teya_transfers as payments

class TeyaTransferTests(unittest.TestCase):
    setUp=autonomy.AdminAutonomyTests.setUp
    tearDown=autonomy.AdminAutonomyTests.tearDown
    post=autonomy.AdminAutonomyTests.post
    order=autonomy.AdminAutonomyTests.order
    store=teya_tests.TeyaPaymentTests.store
    config=teya_tests.TeyaPaymentTests.config
    webhook=teya_tests.TeyaPaymentTests.webhook
    success=teya_tests.TeyaPaymentTests.success
    @classmethod
    def setUpClass(cls):
        teya_tests.TeyaPaymentTests.setUpClass.__func__(cls)

    def prepare(self):
        self.config()
        db.session.add(PaymentGatewayConfiguration(id=1,provider='teya'))
        db.session.add(Setting(key='transfer_enabled',value='true'))
        order=self.order('paid');generate_tickets_for_order(order);db.session.commit()
        ticket=Ticket.query.one(); original=ticket.code
        self.assertEqual(self.post('/ticket-transfer',{'code':original,'email':order.email}).status_code,302)
        payload=json.loads(WebsiteJob.query.filter_by(kind='email').one().payload)
        token=parse_qs(urlsplit(re.search(r'https?://\S+',payload['text'])[0]).query)['token'][0]
        session_id=str(uuid.uuid4())
        with patch.object(gateway,'api',return_value={'session_id':session_id,'session_url':'https://checkout.teya.xyz/session'}) as api:
            r=self.post('/ticket-transfer/accept',{'token':token,'name':'Novo titular','email':'new@example.invalid','accept_terms':'yes'})
        self.assertEqual(r.status_code,303)
        payload=api.call_args.args[2]
        self.assertEqual(payload['amount'],{'value':500,'currency':'EUR'})
        self.assertNotIn('token=',payload['success_url'])
        return ticket,TicketTransfer.query.one(),TransferCheckout.query.one(),token,original

    def result(self,payment,**changes):
        return self.success(payment,amount={'value':500,'currency':'EUR'},**changes)

    def notify(self,payment,**changes):
        data={'session_id':payment.session_id,'merchant_reference':payment.reference,
            'amount':{'value':500,'currency':'EUR'},'status':'SUCCESS','transaction_id':'tr_synthetic-transaction'}
        data.update(changes)
        return self.webhook(payment,data=data)

    def test_signed_success_rotates_qr_once_and_survives_provider_switch(self):
        ticket,transfer,payment,token,old=self.prepare()
        db.session.get(PaymentGatewayConfiguration,1).provider='stripe';db.session.commit()
        with patch.object(gateway,'api',return_value=self.result(payment)):
            self.assertEqual(self.notify(payment).status_code,200)
            new=ticket.code
            self.assertEqual(self.notify(payment).status_code,200)
        self.assertNotEqual(new,old);self.assertEqual(ticket.code,new)
        self.assertEqual(ticket.holder_email,'new@example.invalid')
        self.assertEqual(transfer.status,'completed')
        self.assertEqual(ticket.previous_codes.strip(),old)
        self.assertEqual(EmailDelivery.query.count(),1)
        self.assertEqual(WebsiteJob.query.filter_by(kind='transfer_refund').count(),0)

    def test_unpaid_return_failed_callback_and_amount_mismatch_never_change_qr(self):
        ticket,transfer,payment,token,old=self.prepare()
        self.assertEqual(self.client.get('/ticket-transfer/return?payment_status=SUCCESS').status_code,200)
        with patch.object(gateway,'api',return_value=self.result(payment,session_status='ACTIVE',payment_status='NONE')):
            payments.reconcile(payment.id)
        self.assertEqual(ticket.code,old);self.assertEqual(transfer.status,'pending_payment')
        with patch.object(gateway,'api',return_value={**self.result(payment),'amount':{'value':1,'currency':'EUR'}}):
            self.assertEqual(self.notify(payment).status_code,400)
        with patch.object(gateway,'api') as api:
            self.assertEqual(self.webhook(payment,signature=False).status_code,400);api.assert_not_called()
        self.assertEqual(ticket.code,old)
        with patch.object(gateway,'api',return_value=self.result(payment,session_status='EXPIRED',payment_status='NONE')):
            payments.reconcile(payment.id)
        self.assertEqual(transfer.status,'cancelled');self.assertIsNone(transfer.active_ticket_id)

    def test_paid_used_ticket_requires_manual_teya_refund_without_stripe_job(self):
        ticket,transfer,payment,token,old=self.prepare()
        ticket.checked_in_at=datetime.utcnow();db.session.commit()
        with patch.object(gateway,'api',return_value=self.result(payment)):
            self.assertEqual(self.notify(payment).status_code,200)
            self.assertEqual(self.notify(payment).status_code,200)
        self.assertEqual(ticket.code,old);self.assertEqual(transfer.status,'refund_pending')
        self.assertEqual(payment.status,'paid')
        self.assertEqual(WebsiteJob.query.filter_by(kind='transfer_refund').count(),0)

    def test_retry_uses_same_session_and_wrong_store_rejects(self):
        ticket,transfer,payment,token,old=self.prepare()
        with patch.object(gateway,'api') as api:
            self.assertEqual(self.post('/ticket-transfer/accept',{'token':token}).status_code,303)
            api.assert_not_called()
        self.app.config['TEYA_STORE_ID']=str(uuid.uuid4())
        with patch.object(gateway,'api') as api:
            self.assertEqual(self.notify(payment).status_code,503);api.assert_not_called()
        self.assertEqual(ticket.code,old)

    def test_signed_success_changes_qr_once_even_after_provider_switch_and_never_trusts_return(self):
        ticket,transfer,payment,token,old=self.prepare()
        self.client.get('/ticket-transfer/return?payment_status=SUCCESS')
        self.assertEqual(ticket.code,old)
        db.session.get(PaymentGatewayConfiguration,1).provider='stripe';db.session.commit()
        with patch.object(gateway,'api',return_value=self.result(payment)):
            self.assertEqual(self.webhook(payment,data={'session_id':payment.session_id,'merchant_reference':payment.reference,
                'amount':{'value':500,'currency':'EUR'},'status':'SUCCESS','transaction_id':'tr_synthetic-transaction'}).status_code,200)
            changed=ticket.code
            self.assertNotEqual(changed,old)
            payments.reconcile(payment.id)
        self.assertEqual(ticket.code,changed)
        self.assertIn(old,ticket.previous_codes)
        self.assertEqual(ticket.holder_email,'new@example.invalid')
        self.assertEqual(EmailDelivery.query.count(),1)
        self.assertEqual(transfer.status,'completed')

    def test_timeout_during_session_creation_retries_same_durable_idempotency_key(self):
        ticket,transfer,payment,token,old=self.prepare()
        payment.session_id=None;payment.session_url='';payment.status='preparing';db.session.commit()
        with patch.object(gateway,'api',side_effect=gateway.TeyaUnavailable()):
            self.assertEqual(self.post('/ticket-transfer/accept',{'token':token}).status_code,503)
        self.assertEqual(ticket.code,old)
        answer={'session_id':str(uuid.uuid4()),'session_url':'https://checkout.teya.xyz/synthetic'}
        # Use the exact host declared for the configured staging environment.
        answer['session_url']='https://'+gateway.environment_hosts()[2]+'/synthetic'
        with patch.object(gateway,'api',return_value=answer) as api:
            self.assertEqual(self.post('/ticket-transfer/accept',{'token':token}).status_code,303)
        self.assertEqual(api.call_args.args[3],payment.id)
        self.assertEqual(TransferCheckout.query.count(),1)
