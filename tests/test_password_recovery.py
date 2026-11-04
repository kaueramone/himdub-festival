"""Recovery must not become an alternative way around MFA or revocation."""
from datetime import datetime, timedelta
import json
import re
import unittest
from unittest.mock import patch
from werkzeug.security import check_password_hash, generate_password_hash
import test_admin_autonomy as autonomy
import test_purchase_security as base
from extensions import db
from models import AdminUser, AdminPasswordReset, WebsiteJob, CustomerAccessToken, AuditEvent
from password_recovery import digest


class PasswordRecoveryTests(unittest.TestCase):
    setUp = autonomy.AdminAutonomyTests.setUp
    tearDown = autonomy.AdminAutonomyTests.tearDown
    post = base.PurchaseSecurityTests.post
    secret = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP'
    password = 'a new synthetic password'

    def staff(self, active=True, mfa=True, role='admin'):
        user = AdminUser(email='staff@example.invalid', active=active, role=role,
                         password_hash=generate_password_hash('old synthetic password'),
                         totp_secret=self.secret if mfa else '')
        db.session.add(user)
        db.session.commit()
        return user

    def request_link(self):
        response = self.post('/admin/forgot-password', {'email': ' STAFF@example.invalid '})
        self.assertEqual(response.status_code, 200)
        job = WebsiteJob.query.filter_by(kind='email').first()
        payload = json.loads(job.payload)
        return re.search(r'/admin/reset-password/([A-Za-z0-9_-]{43})', payload['text'])[1]

    def submit(self, token, **overrides):
        form = {'password': self.password, 'password_confirm': self.password,
                'totp': autonomy.code(self.secret)}
        form.update(overrides)
        return self.post('/admin/reset-password/' + token, form)

    def test_generic_response_cooldown_hash_only_and_branded_email(self):
        user = self.staff()
        unknown = self.post('/admin/forgot-password', {'email': 'unknown@example.invalid'})
        self.assertEqual(AdminPasswordReset.query.count(), 0)
        known = self.post('/admin/forgot-password', {'email': user.email})
        self.assertEqual(known.data, unknown.data)
        token = self.request_link()
        row = AdminPasswordReset.query.one()
        self.assertEqual(row.token_hash, digest(token))
        self.assertNotEqual(row.token_hash, token)
        self.assertEqual(row.session_version, user.session_version)
        self.assertEqual(WebsiteJob.query.count(), 1)
        payload = json.loads(WebsiteJob.query.one().payload)
        self.assertEqual(payload['to'], [user.email])
        self.assertIn('logo-him-dub.png', payload['html'])
        self.assertIn('http://localhost/admin/reset-password/', payload['text'])

    def test_inactive_user_never_receives_link(self):
        self.staff(active=False)
        self.assertEqual(self.post('/admin/forgot-password', {'email':'staff@example.invalid'}).status_code, 200)
        self.assertEqual(AdminPasswordReset.query.count(), 0)
        self.assertEqual(WebsiteJob.query.count(), 0)

    def test_get_does_not_consume_and_csrf_is_required(self):
        self.staff()
        token = self.request_link()
        response = self.client.get('/admin/reset-password/' + token)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        self.assertIsNone(AdminPasswordReset.query.one().used_at)
        self.assertEqual(self.client.post('/admin/reset-password/' + token, data={'password':self.password}).status_code, 400)
        self.assertEqual(self.client.post('/admin/forgot-password', data={'email':'staff@example.invalid'}).status_code, 400)

    def test_password_validation_and_mfa_cannot_be_bypassed(self):
        user = self.staff()
        token = self.request_link()
        for overrides in ({'password':'short', 'password_confirm':'short'},
                          {'password_confirm':'a different password'}, {'totp':''},
                          {'password':'x'*257, 'password_confirm':'x'*257}):
            self.assertEqual(self.submit(token, **overrides).status_code, 400)
            db.session.refresh(user)
            self.assertTrue(check_password_hash(user.password_hash, 'old synthetic password'))
            self.assertIsNone(AdminPasswordReset.query.one().used_at)

    def test_success_revokes_sessions_other_links_and_does_not_login(self):
        user = self.staff()
        token = self.request_link()
        other = 'Z'*43
        db.session.add(AdminPasswordReset(token_hash=digest(other),user_id=user.id,
                       session_version=user.session_version,expires_at=datetime.utcnow()+timedelta(minutes=20)))
        db.session.commit()
        old_version = user.session_version
        existing = self.app.test_client()
        with existing.session_transaction() as session:
            session.update(is_admin=True,user_id=user.id,session_version=old_version,admin_role='admin')
        response = self.submit(token)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/admin/login'))
        db.session.refresh(user)
        self.assertTrue(check_password_hash(user.password_hash,self.password))
        self.assertEqual(user.session_version, old_version+1)
        self.assertEqual(user.totp_secret,self.secret)
        self.assertIsNotNone(db.session.get(AdminPasswordReset,digest(token)).used_at)
        self.assertEqual(existing.get('/admin/dashboard').status_code,302)
        self.assertEqual(self.submit(token).status_code,410)
        self.assertEqual(self.submit(other).status_code,410)
        with self.client.session_transaction() as session:
            self.assertNotIn('is_admin',session)
        self.assertEqual(AuditEvent.query.filter_by(action='password_reset').count(),1)
        jobs=[json.loads(job.payload) for job in WebsiteJob.query.all()]
        self.assertTrue(any('Palavra-passe alterada' in x['subject'] for x in jobs))
        self.assertFalse(any(self.password in x['html'] or self.password in x['text'] for x in jobs))
        # Resetting does not disable MFA: a subsequent login still needs a new
        # authenticator code, even though the new password is already valid.
        import time
        with patch('auth.time.time', return_value=time.time()+30):
            new_code=autonomy.code(self.secret)
            self.assertEqual(self.post('/admin/login',{'email':user.email,'password':self.password,'totp':''}).status_code,401)
            self.assertEqual(self.post('/admin/login',{'email':user.email,'password':self.password,'totp':new_code}).status_code,302)

    def test_expired_revoked_and_version_changed_links_fail(self):
        user=self.staff()
        token=self.request_link()
        row=AdminPasswordReset.query.one()
        row.expires_at=datetime.utcnow()-timedelta(seconds=1)
        db.session.commit()
        self.assertEqual(self.submit(token).status_code,410)
        row.expires_at=datetime.utcnow()+timedelta(minutes=20)
        user.active=False
        db.session.commit()
        self.assertEqual(self.submit(token).status_code,410)
        user.active=True
        user.session_version+=1
        db.session.commit()
        self.assertEqual(self.submit(token).status_code,410)
        self.assertEqual(self.client.get('/admin/reset-password/malformed').status_code,404)

    def test_existing_totp_cannot_be_reused_and_missing_admin_mfa_not_bypassed(self):
        user=self.staff()
        token=self.request_link()
        from auth import totp_counter
        user.totp_last_counter=totp_counter(self.secret,autonomy.code(self.secret))
        db.session.commit()
        self.assertEqual(self.submit(token).status_code,400)
        user.totp_secret=''
        db.session.commit()
        self.app.config['PRODUCTION']=True
        self.assertEqual(self.submit(token).status_code,400)

    def test_checkin_without_mfa_keeps_role(self):
        user=self.staff(mfa=False,role='checkin')
        token=self.request_link()
        self.assertEqual(self.submit(token,totp='').status_code,302)
        db.session.refresh(user)
        self.assertEqual(user.role,'checkin')
        self.assertEqual(user.totp_secret,'')

    def test_customer_recovery_uses_existing_verified_email_flow(self):
        response=self.client.get('/account/login')
        self.assertIn(b'/account/recover',response.data)
        response=self.client.get('/account/recover')
        self.assertIn('não utiliza palavra-passe',response.text)
        response=self.post('/account/recover',{'email':'buyer@example.invalid'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(CustomerAccessToken.query.count(),1)
        self.assertEqual(AdminPasswordReset.query.count(),0)
        self.assertEqual(self.client.get('/account/').status_code,302)

    def test_recovery_request_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.post('/admin/forgot-password',{'email':'unknown@example.invalid'}).status_code,200)
        self.assertEqual(self.post('/admin/forgot-password',{'email':'unknown@example.invalid'}).status_code,429)

    def test_concurrent_different_tokens_only_one_password_change(self):
        if db.engine.dialect.name!='postgresql':
            self.skipTest('Recovery serialization requires PostgreSQL')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        user=self.staff()
        tokens=['A'*43,'B'*43]
        for token in tokens:
            db.session.add(AdminPasswordReset(token_hash=digest(token),user_id=user.id,
                session_version=user.session_version,expires_at=datetime.utcnow()+timedelta(minutes=20)))
        db.session.commit()
        clients=[self.app.test_client(),self.app.test_client()]
        barrier=Barrier(2)
        def reset(index):
            barrier.wait(timeout=10)
            return self.post('/admin/reset-password/'+tokens[index],
                {'password':self.password,'password_confirm':self.password,'totp':autonomy.code(self.secret)},clients[index]).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(reset,range(2)))
        self.assertEqual(sorted(results),[302,410])
        db.session.expire_all()
        self.assertEqual(AdminPasswordReset.query.filter(AdminPasswordReset.used_at.isnot(None)).count(),1)

    def test_access_logger_redacts_reset_tokens(self):
        import sys
        if sys.platform == 'win32':
            self.skipTest('Gunicorn runs on Linux in production and CI')
        import runpy
        from pathlib import Path
        logger=runpy.run_path(str(Path(__file__).resolve().parents[1]/'gunicorn.conf.py'))
        from gunicorn.glogging import Logger
        with patch.object(Logger,'atoms',return_value={'U':'/admin/reset-password/private-token'}):
            instance = object.__new__(logger['PrivatePathLogger'])
            self.assertEqual(instance.atoms(None,None,None,None)['U'], '/admin/reset-password/[private]')
