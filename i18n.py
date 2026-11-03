"""Internacionalização (PT / EN / FR).

Como funciona, em resumo:

- O português é o idioma por defeito e não tem prefixo no URL
  (himdubfestival.com/tickets).
- Inglês e francês vivem debaixo de um prefixo (/en/tickets, /fr/tickets).
  Isto é tratado por um pequeno middleware WSGI (`LanguagePrefixMiddleware`,
  registado em app.py) que remove o prefixo do caminho ANTES do Flask fazer
  o "routing" -- por isso nenhuma rota em blueprints/*.py precisa de saber
  nada sobre idiomas.
- `localized_url_for` substitui o `url_for` nos templates (e em partes do
  código Python que fazem redirect) para que todos os links gerados no site
  mantenham automaticamente o prefixo de idioma certo, sem precisar de
  mudar `url_for(...)` em cada sítio onde é chamado.
- O texto em si é traduzido com Flask-Babel: todo o texto fixo nos
  templates está marcado com `{{ _('...') }}` / `{% trans %}`, e as
  traduções em falta (tudo o que não é português, que é a língua em que o
  texto foi escrito de origem) vivem em `translations/en/` e
  `translations/fr/` (ficheiros .po/.mo, geridos com `pybabel`).
"""

from flask import g, request, url_for as flask_url_for
from urllib.parse import urlencode

LANGUAGES = ["pt", "en", "fr"]
DEFAULT_LANGUAGE = "pt"
LANGUAGE_LABELS = {"pt": "Pt", "en": "En", "fr": "Fr"}
LANGUAGE_NAMES = {"pt": "Português", "en": "English", "fr": "Français"}

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
    query = urlencode(list(request.args.items(multi=True)))
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
