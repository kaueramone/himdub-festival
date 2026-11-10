"""Read prebuilt public translations; no translation engine runs on Render.

GitHub Actions builds this website's translated editorial assets with the
open-source LibreTranslate engine. No visitor data enters the translation task.
"""
import json
from pathlib import Path
import threading
import requests
from flask import current_app, g, has_request_context
from flask_babel import force_locale, gettext as babel_gettext, ngettext as babel_ngettext
from translation_terms import is_fixed
from translation_codec import TARGETS, cache_key, validate_translation, glossary_version

TRANSLATIONS_URL = 'https://raw.githubusercontent.com/kaueramone/himdub-festival/site-translations/translations.json'
MAX_TRANSLATIONS_BYTES = 4000000


def cached_text(source, language):
    state = current_app.extensions['public_translations']['values']
    if has_request_context():
        state = g.setdefault('_translation_snapshot', state)
    return state.get(cache_key(source, language))


def translate_public(source, language=None, *, legacy=''):
    from i18n import current_lang
    source = source or ''
    language = language or current_lang()
    if not source or language == 'pt' or is_fixed(source):
        return source
    if language not in TARGETS:
        language = 'en'
    translated = cached_text(source, language)
    if translated:
        return translated
    with force_locale(language):
        translated = babel_gettext(source)
    if translated != source:
        return translated
    if legacy:
        return legacy
    if language != 'en':
        translated = cached_text(source, 'en')
        if translated:
            return translated
        with force_locale('en'):
            return babel_gettext(source)
    return source


def gettext(source, **variables):
    # Interpolate locally: names, email addresses and private URLs never enter
    # the public translation asset or the translation engine.
    text = translate_public(source)
    return text % variables if variables else text


def public_sources():
    """Explicit allowlist of public editorial sources, not rendered HTML."""
    from babel.messages.pofile import read_po
    from models import NativeContent, PageBlock, Product, GalleryAlbum
    from content_catalog import DEFAULT_CONTENT
    path = Path(current_app.root_path) / 'translations/en/LC_MESSAGES/messages.po'
    with path.open('rb') as stream:
        catalogue = read_po(stream)
    sources = {entry.id for entry in catalogue if isinstance(entry.id, str)}
    sources.update(('Momentos junto ao rio. Escolhe um álbum e volta a viver o festival.',
                    'Todos os álbuns', 'As memórias da próxima edição chegam em breve.',
                    'Fotografia ampliada', 'Fechar fotografia', 'Fotografia anterior',
                    'Fotografia seguinte'))
    originals = {item['key']: dict(item) for items in DEFAULT_CONTENT.values() for item in items}
    for row in NativeContent.query.all():
        originals.setdefault(row.key, {}).update(json.loads(row.payload))
    for item in originals.values():
        if item.get('published', True):
            sources.update(item.get(field, '') for field in ('title', 'text', 'link_label'))
    for block in PageBlock.query.filter_by(published=True):
        sources.update((block.title, block.text, block.link_label))
    for product in Product.query.filter_by(is_active=True):
        sources.update((product.name, product.short_description, product.description))
    for album in GalleryAlbum.query.filter_by(published=True):
        sources.update((album.title, album.description))
        sources.update(photo.caption for photo in album.photos)
    return sorted(text for text in sources if text and not is_fixed(text))


def refresh_translations(app):
    with requests.get(TRANSLATIONS_URL, timeout=(3, 15), stream=True,
                      allow_redirects=False) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > MAX_TRANSLATIONS_BYTES:
                raise ValueError('Translation asset too large')
            chunks.append(chunk)
    document = json.loads(b''.join(chunks))
    if document.get('version') != 1 or document.get('glossary') != glossary_version():
        raise ValueError('Translation glossary mismatch')
    values = {}
    for entry in document['entries']:
        source = entry['source']
        if not isinstance(source, str) or len(source) > 20000:
            raise ValueError('Invalid translation source')
        for language, text in entry['translations'].items():
            if language not in TARGETS:
                raise ValueError('Invalid translation language')
            validate_translation(source, text)
            values[cache_key(source, language)] = text
    app.extensions['public_translations']['values'] = values
    return len(values)


def init_translations(app):
    app.extensions['public_translations'] = {'values': {}}
    app.jinja_env.install_gettext_callables(gettext, babel_ngettext, newstyle=True)
    app.jinja_env.globals['translate_public'] = translate_public

    @app.get('/i18n/sources.json')
    def translation_sources():
        # Drafts, users, orders, tokens, buyer emails and admin forms excluded.
        return {'version': 1, 'glossary': glossary_version(), 'sources': public_sources()}, 200, {
            'Cache-Control': 'public, max-age=60', 'X-Robots-Tag': 'noindex'}

    @app.cli.command('translations-refresh')
    def refresh():
        """Fetch and validate this website's generated language assets."""
        import click
        click.echo(f'{refresh_translations(app)} translations loaded.')

    if app.testing or not app.config.get('EMAIL_WORKER_ENABLED'):
        return

    def consume():
        stop = threading.Event()
        while not stop.wait(5):
            try:
                refresh_translations(app)
            except Exception:
                app.logger.warning('Language assets temporarily unavailable; keeping previous translations')
            if stop.wait(295):
                break
    threading.Thread(target=consume, name='public-translations', daemon=True).start()
