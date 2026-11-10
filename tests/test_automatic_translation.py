"""Editorial translation boundaries, language selection and free asset builds."""
import json
import unittest
from unittest.mock import Mock, patch
from flask_babel import refresh
import test_purchase_security as base
import test_admin_autonomy as autonomy
from extensions import db
from models import NativeContent, PageBlock, Product
from auto_translation import refresh_translations, translate_public, start_translation_worker
from translation_codec import cache_key, glossary_version, segments, validate_translation
from translation_terms import FIXED_TERMS


class AutomaticTranslationTests(unittest.TestCase):
    setUp = autonomy.AdminAutonomyTests.setUp
    tearDown = autonomy.AdminAutonomyTests.tearDown
    post = base.PurchaseSecurityTests.post
    login = autonomy.AdminAutonomyTests.login

    def test_fixed_names_survive_all_four_languages(self):
        for language in ('pt', 'en', 'fr', 'es'):
            refresh()
            response = self.client.get(('/' + language if language != 'pt' else '') + '/the-experience')
            self.assertEqual(response.status_code, 200)
            for name in ('The Experience', 'Tickets', 'Participate'):
                self.assertIn(name, response.text)
            for code in ('PT', 'EN', 'FR', 'ES'):
                self.assertIn('>' + code + '</a>', response.text)
        self.assertNotIn('L&#39;Expérience', response.text)
        self.assertIn('<a href="/es/tickets">', response.text)
        self.assertNotIn('&lt;a href=', response.text)

    def test_source_manifest_never_contains_drafts_or_customer_data(self):
        secret = 'UNPUBLISHED-EDITORIAL-SECRET'
        db.session.add(NativeContent(key='rules:0', page_key='rules', payload=json.dumps({
            'kind': 'legal', 'title': secret, 'text': secret, 'published': False, 'sort_order': 0})))
        db.session.add(PageBlock(page_key='experience', title=secret, text=secret, published=False))
        db.session.add(Product(id=3, slug='private-draft', category='ticket', name=secret,
                               price_cents=1000, is_active=False))
        db.session.add(PageBlock(page_key='experience', title='Published editorial title', published=True))
        db.session.commit()
        order = self.order() if hasattr(self, 'order') else base.PurchaseSecurityTests.order(self)
        response = self.client.get('/i18n/sources.json')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(secret, response.text)
        self.assertNotIn(order.email, response.text)
        self.assertNotIn('A organização deve', response.text)
        self.assertIn('Published editorial title', response.json['sources'])

    def test_assets_are_validated_atomically_and_requests_make_no_api_calls(self):
        document = {'version': 1, 'glossary': glossary_version(), 'entries': [{
            'source': 'Faz parte do HIM Dub', 'translations': {'es': 'Forma parte de HIM Dub'}}]}
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [json.dumps(document).encode()]
        with patch('auto_translation.requests.get', return_value=response) as fetch:
            self.assertEqual(refresh_translations(self.app), 1)
        refresh()
        public = self.client.get('/es/participate')
        self.assertIn('Forma parte de HIM Dub', public.text)
        fetch.assert_called_once()
        previous = self.app.extensions['public_translations']['values']
        document['entries'][0]['translations']['es'] = '<script>injection()</script>'
        response.iter_content.return_value = [json.dumps(document).encode()]
        with patch('auto_translation.requests.get', return_value=response):
            with self.assertRaises(ValueError):
                refresh_translations(self.app)
        self.assertIs(self.app.extensions['public_translations']['values'], previous)

    def test_names_markup_and_placeholders_never_enter_engine(self):
        source = 'Conhece The Experience e Tickets: <a href="%(url)s">HIM Dub</a>. Custa 10,50 €.'
        parts = segments(source)
        translatable = ''.join(value for protected, value in parts if not protected)
        self.assertNotIn('Tickets', translatable)
        self.assertNotIn('Experience', translatable)
        self.assertNotIn('href', translatable)
        self.assertNotIn('%(url)', translatable)
        self.assertNotIn('10,50', translatable)
        self.assertEqual(''.join(value for _, value in parts), source)
        self.assertEqual(validate_translation(source, source), source)
        for broken in (source.replace('Tickets', 'Billets'), source.replace('%(url)s', 'URL'),
                       source + '<script>bad()</script>', source.replace('10,50', '50,10')):
            with self.assertRaises(ValueError):
                validate_translation(source, broken)

    def test_explicit_choice_wins_and_unknown_browser_language_defaults_to_english(self):
        client = self.app.test_client()
        self.assertEqual(client.get('/', headers={'Accept-Language': 'de-DE,fr;q=0.8'}).location, '/en/')
        self.assertEqual(client.get('/', headers={'Accept-Language': 'es-MX'}).location, '/es/')
        chosen = client.get('/?language=pt', headers={'Accept-Language': 'de-DE'})
        self.assertEqual(chosen.status_code, 200)
        self.assertIn('himdub_language=pt', chosen.headers['Set-Cookie'])
        self.assertEqual(client.get('/', headers={'Accept-Language': 'de-DE'}).status_code, 200)
        self.assertEqual(client.get('/fr/').status_code, 200)
        self.assertEqual(client.get('/checkout').status_code, 302)  # empty cart; no locale redirect
        self.assertIn('private', chosen.headers['Cache-Control'])

    def test_cms_has_one_source_and_does_not_accept_old_translation_inputs(self):
        self.login()
        editor = self.client.get('/admin/content/original/edit?key=experience:festival')
        self.assertNotIn('name="title_en"', editor.text)
        self.assertNotIn('name="text_fr"', editor.text)
        self.post('/admin/content/original/edit', {'key': 'experience:festival', 'title': 'Novo conteúdo',
            'title_en': 'Stale injected translation', 'text': 'Descrição actual', 'published': 'on'})
        payload = json.loads(NativeContent.query.one().payload)
        self.assertNotIn('Stale injected translation', json.dumps(payload))
        self.assertNotIn('Stale injected translation', self.client.get('/en/the-experience').text)

    def test_country_lookup_uses_local_database_and_unsupported_country_is_english(self):
        import ipaddress
        reader = self.app.extensions['country_geolocation']
        addresses = {}
        for network, record in reader:
            country = record.get('country', {}).get('iso_code')
            address = network.network_address
            if country in {'PT', 'FR', 'ES', 'DE'} and address.is_global:
                addresses.setdefault(country, str(address))
                if len(addresses) == 4:
                    break
        self.assertEqual(len(addresses), 4)
        for country, language in [('PT', 'pt'), ('FR', 'fr'), ('ES', 'es'), ('DE', 'en')]:
            client = self.app.test_client()
            response = client.get('/', environ_overrides={'REMOTE_ADDR': addresses[country]},
                                  headers={'Accept-Language': 'pt-PT'})
            if language == 'pt':
                self.assertEqual(response.status_code, 200)
            else:
                self.assertEqual(response.location, '/' + language + '/')

    def test_source_changes_do_not_reuse_old_translation(self):
        self.app.extensions['public_translations']['values'] = {cache_key('Texto original', 'es'): 'Texto traducido'}
        with self.app.test_request_context('/es/'):
            self.assertEqual(translate_public('Texto original', 'es'), 'Texto traducido')
            self.assertEqual(translate_public('Texto alterado', 'es'), 'Texto alterado')

    def test_reviewed_interface_labels_take_priority_over_ambiguous_machine_output(self):
        self.app.extensions['public_translations']['values'] = {
            cache_key('Pagar', 'fr'): 'Rémunération',
            cache_key('Subscrever', 'es'): 'Subscrever'}
        with self.app.test_request_context('/fr/'):
            self.assertEqual(translate_public('Pagar', 'fr'), 'Payer')
            self.assertEqual(translate_public('Subscrever', 'es'), 'Suscribirse')

    def test_refresher_starts_in_each_process_once_even_after_preload_fork(self):
        self.app.config.update(TESTING=False, TRANSLATION_WORKER_ENABLED=True)
        self.app.extensions['public_translations'].pop('worker_pid', None)
        with patch('auto_translation.threading.Thread') as thread:
            with patch('auto_translation.os.getpid', return_value=111):
                start_translation_worker(self.app)
                start_translation_worker(self.app)
                self.assertEqual(thread.return_value.start.call_count, 1)
            # The forked worker inherits the state, but not the master thread.
            with patch('auto_translation.os.getpid', return_value=222):
                start_translation_worker(self.app)
                self.assertEqual(thread.return_value.start.call_count, 2)
