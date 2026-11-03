"""Ponto de entrada da aplicacao Flask.

Para correr localmente (dentro do PyCharm ou na linha de comandos):
    python app.py

A app fica disponivel em http://127.0.0.1:5000
"""

from flask import Flask, g, request, url_for
import hashlib
from pathlib import Path
from flask_babel import Babel
from config import Config
from extensions import db
from site_lock import check_site_lock
from coming_soon import check_coming_soon
from festival_time import to_local_time
from i18n import (
    LanguagePrefixMiddleware,
    LANGUAGES,
    LANGUAGE_LABELS,
    LANGUAGE_NAMES,
    DEFAULT_LANGUAGE,
    current_lang,
    get_locale,
    localized_url_for,
    lang_url_for_path,
)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    asset_files=['js/main.js','js/admin.js','js/gallery.js','js/admin_tickets.js','css/admin.css','css/managed.css']
    app.jinja_env.globals['asset_version']=hashlib.sha256(b''.join(
        (Path(app.static_folder)/name).read_bytes() for name in asset_files)).hexdigest()[:12]
    from security import init_security
    init_security(app)
    from auth import init_auth
    init_auth(app)
    if app.config["PRODUCTION"]:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.config.setdefault("LANGUAGES", LANGUAGES)
    app.config.setdefault("BABEL_DEFAULT_LOCALE", DEFAULT_LANGUAGE)
    app.config.setdefault("BABEL_TRANSLATION_DIRECTORIES", "translations")

    # /en/... e /fr/... perdem o prefixo antes de chegar ao Flask; o
    # português fica sem prefixo (/tickets, etc). Ver i18n.py.
    app.wsgi_app = LanguagePrefixMiddleware(app.wsgi_app, ["en", "fr"])

    db.init_app(app)
    from cms import page_context
    app.context_processor(page_context)
    Babel(app, locale_selector=get_locale)

    @app.before_request
    def enforce_site_lock():
        # Corre antes de tudo o resto -- se o bloqueio estiver ligado (ver
        # site_lock.py) e faltar a password certa, pedimos login antes de
        # sequer chegar a qualquer pagina/rota do site.
        return check_site_lock()

    # Ficheiros estaticos que praticamente nunca mudam (videos de fundo,
    # audio, imagens, fontes) -- sao de longe o maior consumo de bandwidth
    # do site (os 3 videos de fundo sao 5-11MB cada). Sem isto, o browser
    # continua a perguntar ao servidor em cada recarregar de pagina "isto
    # mudou?" (recebe so um 304, sem corpo -- barato, mas nao e gratis: e
    # sempre mais um pedido). Com Cache-Control: max-age, o browser nem
    # chega a perguntar durante esse periodo, poupando o pedido inteiro --
    # importante quando alguem abre e fecha o site varias vezes seguidas
    # (ex: a pensar se compra o bilhete) sem fazer hard-refresh.
    _STATIC_LONG_CACHE_PREFIXES = (
        "/static/video/",
        "/static/audio/",
        "/static/img/",
        "/static/fonts/",
    )

    @app.after_request
    def add_static_cache_headers(response):
        # So mexemos em respostas 200 -- um 401 (ex: bloqueio do site por
        # password, ver site_lock.py) nunca deve ficar marcado como
        # "public, guarda isto" em caches/proxies pelo caminho.
        if response.status_code != 200:
            return response
        path = request.path
        if path.startswith(_STATIC_LONG_CACHE_PREFIXES):
            # 2 dias -- suficiente para poupar bandwidth em quem reabre o
            # site varias vezes nos dias do lancamento, mas curto o
            # suficiente para nao ficar preso durante semanas se os videos
            # forem substituidos por uma versao de mais qualidade (ex:
            # depois de mudar para o Bunny) -- quem ja tiver a versao
            # antiga em cache so a troca fazendo hard-refresh (Ctrl+Shift+R)
            # ou esperando os 2 dias passarem.
            response.headers["Cache-Control"] = "public, max-age=172800"
        elif path.startswith("/static/"):
            # CSS/JS: cache de 1 dia -- ainda podem mudar durante afinacoes
            # ao site, mas nao vale a pena arriscar tanto tempo de cache
            # como o video, ja que uma correcao urgente (ex: um bug visual)
            # devia chegar a quem ja visitou o site num prazo curto.
            response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    @app.before_request
    def set_request_language():
        g.lang_code = request.environ.get("himdub.lang_code") or DEFAULT_LANGUAGE

    @app.before_request
    def enforce_coming_soon():
        # Corre depois de set_request_language (precisa de g.lang_code já
        # definido, para a página "brevemente" aparecer traduzida) mas
        # antes de qualquer rota -- se o modo estiver ligado (ver
        # coming_soon.py / COMING_SOON_MODE em config.py), mostra a página
        # informativa em vez do site normal.
        return check_coming_soon()

    @app.context_processor
    def inject_i18n():
        return {
            "current_lang": current_lang(),
            "languages": LANGUAGES,
            "language_labels": LANGUAGE_LABELS,
            "language_names": LANGUAGE_NAMES,
            "lang_url": lang_url_for_path,
        }

    # Substitui o url_for global usado pelos templates: assim, TODOS os
    # links gerados nos templates (menus, botões, rodapé, etc.) mantêm
    # automaticamente o prefixo de idioma certo, sem precisar de mudar um
    # único url_for(...) espalhado pelos templates.
    app.jinja_env.globals["url_for"] = localized_url_for

    # Converte horas UTC (como vêm da base de dados) para hora de Portugal
    # antes de aparecerem no admin/check-in -- ver festival_time.py.
    app.jinja_env.filters["local_time"] = to_local_time
    app.jinja_env.filters['money'] = lambda value: f'{float(value):,.2f}'.replace(',', '\u202f').replace('.', ',')
    app.jinja_env.filters['order_status'] = lambda value: {
        'paid':'Pago','comp':'Cortesia','pending':'Por pagar','processing':'Em confirmação',
        'cancelled':'Cancelado','refunded':'Reembolsado','demo-paid':'Teste',
        'awaiting_email':'Por confirmar email','pending_payment':'A aguardar taxa',
        'completed':'Concluído','refund_pending':'Reembolso em curso',
    }.get(value,value)

    def cdn_url(path):
        """URL de um ficheiro estatico "pesado" (video, audio) que pode vir
        do Bunny -- ver CDN_BASE_URL em config.py. 'path' e relativo a
        /static/, ex: 'video/hero-bg.mp4' ou 'audio/bg-music.mp3'. Com o
        Bunny configurado, aponta para la; sem isso, continua a vir do
        Render como sempre foi."""
        base = app.config.get("CDN_BASE_URL", "")
        if base:
            return f"{base}/static/{path}"
        return url_for("static", filename=path)

    app.jinja_env.globals["cdn_url"] = cdn_url

    # Blueprints = grupos de rotas, para nao ter tudo num ficheiro gigante.
    from ticket_sales import website_image, sale_available
    app.jinja_env.globals.update(ticket_sale_image=website_image, ticket_sale_available=sale_available)
    from blueprints.main import main_bp
    from blueprints.shop import shop_bp
    from blueprints.admin import admin_bp
    from blueprints.checkin import checkin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(shop_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(checkin_bp)

    from db_migrate import run as run_migrations
    @app.cli.command("db-upgrade")
    def db_upgrade():
        """Apply additive schema changes after a verified backup."""
        db.create_all()
        run_migrations(app)

    if app.config["AUTO_MIGRATE"]:
        with app.app_context():
            db.create_all()
        run_migrations(app)

    @app.get("/healthz")
    def healthz():
        from sqlalchemy import text
        try:
            db.session.execute(text("SELECT reservation_expires_at FROM orders WHERE 1=0"))
            db.session.execute(text("SELECT id FROM email_deliveries WHERE 1=0"))
        except Exception:
            db.session.rollback()
            return {"status": "unavailable"}, 503, {"Cache-Control": "no-store"}
        return {"status": "ok"}, 200, {"Cache-Control": "no-store"}

    from outbox import init_outbox
    init_outbox(app)

    return app


app = create_app()

if __name__ == "__main__":
    # host="0.0.0.0" permite aceder ao site a partir de outros aparelhos na
    # mesma rede Wi-Fi (ex: o teu telemóvel) -- não só a partir deste PC.
    app.run(debug=True, host="0.0.0.0")
