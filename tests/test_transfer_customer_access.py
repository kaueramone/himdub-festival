"""Ownership, login and receipt regression tests; no external payments or mail."""
from datetime import datetime
from unittest.mock import patch
import unittest
import test_teya_transfers as transfers_tests
import test_teya_payments as payments_tests
import test_admin_autonomy as autonomy
from extensions import db
from models import TicketTransfer, WebsiteJob
import teya_gateway


class TransferCustomerAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payments_tests.TeyaPaymentTests.setUpClass.__func__(cls)

    setUp = autonomy.AdminAutonomyTests.setUp
    tearDown = autonomy.AdminAutonomyTests.tearDown
    post = autonomy.AdminAutonomyTests.post
    order = autonomy.AdminAutonomyTests.order
    config = payments_tests.TeyaPaymentTests.config
    store = payments_tests.TeyaPaymentTests.store
    customer_login = autonomy.AdminAutonomyTests.customer_login
    prepare = transfers_tests.TeyaTransferTests.prepare

    def test_anonymous_cannot_open_or_submit_any_transfer_page(self):
        for path in ('/ticket-transfer', '/ticket-transfer/accept?token=' + 'x'*43,
                     '/ticket-transfer/return'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/account/login', response.location)
            self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(self.post('/ticket-transfer', {'code':'EXAMPLE'}).status_code, 302)
        self.assertEqual(self.post('/ticket-transfer/accept', {'token':'x'*43}).status_code, 302)
        self.assertEqual(TicketTransfer.query.count(), 0)
        self.assertEqual(WebsiteJob.query.count(), 0)

    def test_logged_customer_cannot_use_another_holders_private_link(self):
        ticket, transfer, payment, token, original = self.prepare()
        stranger = self.app.test_client()
        self.customer_login('stranger@example.invalid', stranger)
        with patch.object(teya_gateway, 'api') as api:
            self.assertEqual(stranger.get('/ticket-transfer/accept?token='+token).status_code, 404)
            self.assertEqual(self.post('/ticket-transfer/accept', {'token':token}, stranger).status_code, 404)
            api.assert_not_called()
        self.assertEqual(ticket.code, original)

    def test_posted_email_cannot_override_authenticated_identity(self):
        ticket, transfer, payment, token, original = self.prepare()
        before = WebsiteJob.query.count()
        stranger = self.app.test_client()
        self.customer_login('stranger@example.invalid', stranger)
        self.assertEqual(self.post('/ticket-transfer',
            {'code':original, 'email':transfer.original_email}, stranger).status_code,302)
        self.assertEqual(TicketTransfer.query.count(),1)
        self.assertEqual(WebsiteJob.query.count(),before)

    def test_revoked_session_cannot_use_valid_transfer_link(self):
        ticket, transfer, payment, token, original = self.prepare()
        from models import CustomerAccount
        account = CustomerAccount.query.filter_by(email=transfer.original_email).one()
        account.session_version += 1
        db.session.commit()
        self.assertEqual(self.client.get('/ticket-transfer/accept?token='+token).status_code,302)
        with self.client.session_transaction() as session:
            self.assertEqual(session['customer_transfer_token'],token)

    def test_teya_purchase_receipt_goes_to_buyer(self):
        import uuid
        order, payment = payments_tests.TeyaPaymentTests.payment(self)
        session_id = payment.session_id
        with self.app.test_request_context('/checkout'):
            with patch.object(teya_gateway, 'api',return_value={
                'session_id':session_id,'session_url':'https://checkout.teya.xyz/pay/'+session_id}) as api:
                teya_gateway.create_session(payment)
        self.assertEqual(api.call_args.args[2]['customer_success_email'],order.email)
        self.assertEqual(api.call_args.args[3],payment.id)
