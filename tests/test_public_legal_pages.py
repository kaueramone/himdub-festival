"""Draft privacy boundaries and editing through the existing protected CMS."""
import unittest
from flask_babel import refresh
import test_admin_autonomy as autonomy
import test_purchase_security as base
from models import NativeContent


class PublicLegalPageTests(unittest.TestCase):
    setUp=autonomy.AdminAutonomyTests.setUp
    tearDown=autonomy.AdminAutonomyTests.tearDown
    post=base.PurchaseSecurityTests.post
    login=autonomy.AdminAutonomyTests.login

    def test_legal_drafts_are_not_published_by_visiting_pages(self):
        for page in ('rules','privacy','terms','cookies'):
            response=self.client.get('/infos/'+page)
            self.assertEqual(response.status_code,200)
            self.assertIn('Estamos a actualizar',response.text)
            self.assertNotIn('A organização deve',response.text)
        self.assertEqual(NativeContent.query.count(),0)
        self.assertEqual(self.client.get('/infos/not-a-document').status_code,404)

    def test_admin_can_review_publish_translate_and_hide_legal_section(self):
        url='/admin/content/original/edit?key=rules:0'
        self.assertEqual(self.client.get(url).status_code,302)
        self.login()
        response=self.client.get('/admin/content?page=rules')
        self.assertEqual(response.status_code,200)
        self.assertIn('Rascunho',response.text)
        self.assertIn('Minuta para revisão',self.client.get(url).text)
        form={'title':'Entrada','text':'<script>injection()</script>','title_en':'Admission',
              'text_en':'Admission information','sort_order':'0','published':'on'}
        self.assertEqual(self.client.post(url,data=form).status_code,400)
        self.assertEqual(self.post(url,form).status_code,302)
        response=self.client.get('/infos/rules')
        self.assertIn('&lt;script&gt;',response.text)
        self.assertNotIn('<script>injection()',response.text)
        self.assertNotIn('Admission information',self.client.get('/en/infos/rules').text)
        # Hidden legacy inputs are no longer accepted as manual translations.
        self.assertNotIn('title_en',self.client.get(url).text)
        form.pop('published')
        self.assertEqual(self.post(url,form).status_code,302)
        self.assertNotIn('injection()',self.client.get('/infos/rules').text)

    def test_checkin_cannot_edit_legal_texts(self):
        self.login(role='checkin')
        self.assertEqual(self.post('/admin/content/original/edit?key=privacy:0',
            {'title':'Unauthorised','text':'No','published':'on'}).status_code,302)
        self.assertEqual(NativeContent.query.count(),0)

    def test_footer_is_present_on_home_and_translated(self):
        page=self.client.get('/')
        self.assertIn('id="footer-newsletter-email"',page.text)
        self.assertIn('name="consent"',page.text)
        self.assertIn('required',page.text)
        self.assertIn('aria-label="Instagram"',page.text)
        self.assertNotIn('fonts.googleapis.com',page.text)
        # The fixture holds one app context; clear Babel's per-context locale.
        refresh()
        self.assertIn('Festival news',self.client.get('/en/').text)
        refresh()
        self.assertIn('Les nouvelles du festival',self.client.get('/fr/').text)
        old=self.client.get('/index.php/regras-e-normas-de-funcionamento/')
        self.assertEqual(old.status_code,301)
        self.assertTrue(old.location.endswith('/infos/rules'))
