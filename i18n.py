"""PT/EN/FR/ES routing and private, approximate country language selection.

PT has no URL prefix; EN, FR and ES use prefixes. Public editorial translations
are built offline by LibreTranslate and read from cached assets. Existing Babel
catalogues provide a fallback. Admin/customer tools retain their own PT interface.
"""

from flask import g, request, url_for as flask_url_for
from urllib.parse import urlencode

LANGUAGES = ["pt", "en", "fr", "es"]
DEFAULT_LANGUAGE = "pt"
LANGUAGE_LABELS = {code: code.upper() for code in LANGUAGES}
LANGUAGE_NAMES = {"pt": "Português", "en": "English", "fr": "Français", "es": "Español"}

# Só os blueprints públicos/de conteúdo são traduzidos e recebem prefixo de
# idioma nos links. O /admin e o /checkin ficam sempre em português -- são
# ferramentas internas para a equipa do festival, não para o público.
TRANSLATED_BLUEPRINTS = {"main", "shop"}


def current_lang():
    """O idioma do pedido atual (definido pelo before_request em app.py)."""
    return getattr(g, "lang_code", DEFAULT_LANGUAGE)


def get_locale():
    """Usado pelo Flask-Babel para escolher o ficheiro de traduções."""
    return current_lang()


def localized_url_for(endpoint, **values):
    """Como o url_for do Flask, mas acrescenta o prefixo de idioma certo
    (/en/..., /fr/...) automaticamente quando o endpoint pertence a um dos
    TRANSLATED_BLUEPRINTS e o idioma atual não é o português. Usado como o
    `url_for` global nos templates (ver app.py) para que nenhum template
    precise de saber nada sobre idiomas."""
    path = flask_url_for(endpoint, **values)
    lang = current_lang()
    if lang != DEFAULT_LANGUAGE:
        blueprint = endpoint.split(".")[0] if "." in endpoint else None
        if blueprint in TRANSLATED_BLUEPRINTS:
            path = f"/{lang}{path}"
    return path


def lang_url_for_path(lang):
    """Devolve o URL da página atual (o mesmo `request.path`) no idioma
    indicado -- usado pelo seletor de idioma no cabeçalho/hero."""
    base_path = request.path  # já sem prefixo, o middleware trata disso
    query = urlencode([(key, value) for key, value in request.args.items(multi=True)
                       if key != 'language'] + [('language', lang)])
    suffix = "?" + query if query else ""
    if lang == DEFAULT_LANGUAGE:
        return base_path + suffix
    return f"/{lang}{base_path}" + suffix


class LanguagePrefixMiddleware:
    """Middleware WSGI: se o caminho pedido começar por /en/ ou /fr/,
    remove esse prefixo antes do Flask processar o pedido, e guarda o
    idioma detetado em `environ["himdub.lang_code"]` (lido depois por um
    before_request, ver app.py). Sem prefixo reconhecido, assume português.
    """

    def __init__(self, wsgi_app, languages):
        self.wsgi_app = wsgi_app
        self.languages = set(languages)

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "") or "/"
        segments = path.split("/", 2)  # ex: ['', 'en', 'tickets']
        if len(segments) >= 2 and segments[1] in self.languages:
            environ["himdub.lang_code"] = segments[1]
            environ["PATH_INFO"] = "/" + (segments[2] if len(segments) > 2 else "")
        else:
            environ["himdub.lang_code"] = None
        return self.wsgi_app(environ, start_response)


def init_language_selection(app):
    """Country inference only on entry; explicit language links always win."""
    import ipaddress
    from pathlib import Path
    from flask import redirect
    reader = None
    path = Path(app.config.get('GEOIP_COUNTRY_DATABASE') or
                Path(app.root_path) / 'data/dbip-country-lite.mmdb')
    if path.is_file():
        import maxminddb
        reader = maxminddb.open_database(str(path))
    app.extensions['country_geolocation'] = reader

    portuguese = {'PT', 'BR', 'AO', 'MZ', 'CV', 'GW', 'ST', 'TL'}
    french = {'FR', 'MC', 'BE', 'LU', 'CD', 'CG', 'CI', 'SN', 'ML', 'BF', 'NE',
              'BJ', 'TG', 'GA', 'GN', 'TD', 'CF', 'DJ', 'MG', 'KM', 'RE', 'YT',
              'GP', 'MQ', 'GF', 'PF', 'NC', 'WF', 'PM'}
    spanish = {'ES', 'MX', 'AR', 'BO', 'CL', 'CO', 'CR', 'CU', 'DO', 'EC', 'GQ',
               'GT', 'HN', 'NI', 'PA', 'PE', 'PR', 'PY', 'SV', 'UY', 'VE'}

    def country_language(country):
        if country in portuguese:
            return 'pt'
        if country in french:
            return 'fr'
        if country in spanish:
            return 'es'
        return 'en'

    @app.before_request
    def choose_language():
        if request.blueprint not in TRANSLATED_BLUEPRINTS or request.method != 'GET':
            return
        choice = request.args.get('language')
        explicit = request.environ.get('himdub.lang_code')
        if choice in LANGUAGES:
            g.language_preference = choice
            return
        if explicit:
            return
        # Only infer at the homepage. Existing deep links, callback URLs,
        # checkout mutations and crawler routes retain their canonical locale.
        if request.endpoint != 'main.home':
            return
        choice = request.cookies.get('himdub_language')
        if choice not in LANGUAGES:
            country = None
            try:
                address = ipaddress.ip_address(request.remote_addr)
                if address.is_global and reader:
                    country = (reader.get(str(address)) or {}).get('country', {}).get('iso_code')
            except (ValueError, TypeError):
                pass
            if country:
                choice = country_language(country)
            else:
                # No location available: the visitor's primary browser language
                # is preferable to guessing. Unsupported primary languages -> EN.
                primary = next(iter(request.accept_languages), ('en', 1))[0].split('-')[0].lower()
                choice = primary if primary in LANGUAGES else 'en'
        if choice != DEFAULT_LANGUAGE:
            target = '/' + choice + '/'  # fixed path; never redirect to user-supplied URLs
            query = urlencode(list(request.args.items(multi=True)))
            response = redirect(target + ('?' + query if query else ''), 302)
            response.headers['Cache-Control'] = 'private, no-store'
            response.vary.update(('Cookie', 'Accept-Language'))
            return response

    @app.after_request
    def remember_language(response):
        choice = getattr(g, 'language_preference', None)
        if choice:
            response.set_cookie('himdub_language', choice, max_age=31536000,
                                secure=bool(app.config.get('PRODUCTION')),
                                httponly=True, samesite='Lax')
            response.headers['Cache-Control'] = 'private, no-store'
        if request.endpoint == 'main.home':
            response.vary.update(('Cookie', 'Accept-Language'))
            response.headers['Cache-Control'] = 'private, no-store'
        return response
