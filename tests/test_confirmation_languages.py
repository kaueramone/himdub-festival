"""The real email builder preserves language, escaping and concise guidance."""
import unittest
from types import SimpleNamespace as NS
from pathlib import Path
from flask import Flask
from emailing import build_ticket_email_html, EMAIL_STRINGS

class ConfirmationLanguageTests(unittest.TestCase):
    def test_pt_en_es_and_existing_fr_have_short_complete_copy(self):
        app = Flask(__name__,template_folder=str(Path(__file__).resolve().parents[1]/'templates'))
        expected = {'pt':'A tua encomenda #123 está confirmada.',
                    'en':'Your order #123 is confirmed.',
                    'es':'Tu pedido #123 está confirmado.',
                    'fr':'Ta commande #123 est confirmée.'}
        for language in expected:
            order=NS(lang_code=language,id=123,name='<script>buyer</script>',tickets=[],items=[],total_cents=0,total=0)
            with app.app_context():
                html=build_ticket_email_html(order,'https://himdubfestival.com')
            self.assertIn(expected[language],html)
            self.assertIn('lang="'+language+'"',html)
            self.assertIn(EMAIL_STRINGS[language]['customer_portal_label'],html)
            self.assertIn('/account/login',html)
            self.assertIn('/infos/rules',html)
            self.assertIn('&lt;script&gt;buyer&lt;/script&gt;',html)
            self.assertNotIn('<script>buyer',html)
            self.assertNotIn('non-transferable',html)
            self.assertNotIn('intransmissível',html)
            self.assertLess(len(' '.join(str(v) for v in EMAIL_STRINGS[language].values()).split()),230)
