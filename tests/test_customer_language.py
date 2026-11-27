"""Language belongs to a verified recipient, never to an unverified login form."""
import unittest, json, re
from datetime import datetime, timedelta
import test_customer_portal as portal
import test_admin_autonomy as autonomy
from extensions import db
from models import CustomerAccount, CustomerAccessToken, WebsiteJob, EmailDelivery, Ticket
from emailing import _ticket_email_payload
from ticketing import generate_tickets_for_order
from customer_portal import digest
from outbox import enqueue_order_emails

class CustomerLanguageTests(unittest.TestCase):
    setUp=autonomy.AdminAutonomyTests.setUp
    tearDown=autonomy.AdminAutonomyTests.tearDown
    post=autonomy.AdminAutonomyTests.post
    order=autonomy.AdminAutonomyTests.order
    customer_login=autonomy.AdminAutonomyTests.customer_login

    def test_new_account_language_saved_only_after_email_verification(self):
        self.assertEqual(self.post('/account/login',{'email':'buyer@example.invalid','preferred_language':'es'}).status_code,200)
        self.assertEqual(CustomerAccount.query.count(),0)
        payload=json.loads(WebsiteJob.query.one().payload)
        self.assertIn('Tu acceso privado',payload['subject'])
        self.assertIn('lang="es"',payload['html'])
        token=re.search(r'/account/access/([A-Za-z0-9_-]{43})',payload['text'])[1]
        self.assertEqual(self.post('/account/access/'+token).status_code,302)
        self.assertEqual(CustomerAccount.query.one().preferred_language,'es')

    def test_existing_preference_cannot_be_changed_by_unverified_request(self):
        db.session.add(CustomerAccount(email='buyer@example.invalid',preferred_language='en'));db.session.commit()
        self.post('/account/login',{'email':'buyer@example.invalid','preferred_language':'es'})
        self.assertEqual(CustomerAccount.query.one().preferred_language,'en')
        payload=json.loads(WebsiteJob.query.one().payload)
        self.assertIn('Your private access',payload['subject'])
        token=re.search(r'/account/access/([A-Za-z0-9_-]{43})',payload['text'])[1]
        self.post('/account/access/'+token,{'preferred_language':'es'})
        self.assertEqual(CustomerAccount.query.one().preferred_language,'en')

    def test_verified_profile_changes_future_mail_and_does_not_change_order_language(self):
        order=self.order('paid');order.lang_code='pt'
        generate_tickets_for_order(order);db.session.commit()
        account=self.customer_login(order.email)
        self.assertEqual(self.post('/account/profile',{'name':'Buyer','phone':'','preferred_language':'es'}).status_code,302)
        self.assertEqual(account.preferred_language,'es')
        payload=_ticket_email_payload(order,self.app.config,order.email,order.name,list(order.tickets))
        self.assertIn('Tus entradas',payload['subject'])
        self.assertIn('lang="es"',payload['html'])
        self.assertEqual(order.lang_code,'pt')
        enqueue_order_emails(order);db.session.commit()
        self.assertEqual(EmailDelivery.query.one().language_code,'es')
        account.preferred_language='en';db.session.commit()
        self.assertEqual(EmailDelivery.query.one().language_code,'es')

    def test_invalid_language_is_rejected_and_transfer_login_resumes_private_link(self):
        self.assertEqual(self.post('/account/login',{'email':'buyer@example.invalid','preferred_language':'xx'}).status_code,400)
        self.assertEqual(CustomerAccessToken.query.count(),0)
        transfer_token='x'*43
        self.client.get('/ticket-transfer/accept?token='+transfer_token)
        raw='a'*43
        db.session.add(CustomerAccessToken(token_hash=digest(raw),email='buyer@example.invalid',preferred_language='en',expires_at=datetime.utcnow()+timedelta(minutes=10)))
        db.session.commit()
        response=self.post('/account/access/'+raw)
        self.assertEqual(response.status_code,302)
        self.assertIn('/ticket-transfer/accept?token='+transfer_token,response.location)
