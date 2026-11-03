"""Configuracao da aplicacao Flask.

Tudo o que muda entre o teu computador e o servidor final (chaves secretas,
ligacao a base de dados, chaves do Stripe) vive num ficheiro ".env" que NAO
vai para o Git. Ve o ".env.example" para saberes que variaveis existem.
"""

import os
import re
import secrets
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _session_secret():
    configured = os.environ.get("SECRET_KEY", "").strip()
    if configured:
        return configured
    production = os.environ.get("APP_ENV") == "production" or bool(os.environ.get("RENDER")) or os.environ.get("DOMAIN", "").startswith("https://")
    if production:
        raise RuntimeError("SECRET_KEY must be configured in production.")
    # Development only: never reuse a public, fixed session-signing key.
    return secrets.token_hex(32)


class Config:
    PRODUCTION = os.environ.get("APP_ENV") == "production" or bool(os.environ.get("RENDER")) or os.environ.get("DOMAIN", "").startswith("https://")
    SECRET_KEY = _session_secret()
    ALLOW_LEGACY_LOGIN = not PRODUCTION and os.environ.get("ALLOW_LEGACY_LOGIN", "true").lower() == "true"
    AUTO_MIGRATE = not PRODUCTION

    # Algumas plataformas (ex: Render, Heroku) dao o URL da base de dados
    # Postgres com o prefixo antigo "postgres://", que o SQLAlchemy 1.4+
    # ja nao aceita (exige "postgresql://") -- corrigimos aqui para nao
    # teres de te lembrar disso sempre que ligares uma base de dados nova.
    _database_url = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "himdub.db")
    )
    if _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql://", 1)
    # O Render por vezes fornece a DATABASE_URL ja com "+psycopg" (driver
    # psycopg3), que nao esta instalado -- o requirements.txt so tem o
    # psycopg2-binary (psycopg2). Isto fazia o arranque falhar logo no
    # deploy com "ModuleNotFoundError: No module named 'psycopg'".
    # Forcamos sempre o driver psycopg2, seja qual for o formato recebido.
    _database_url = re.sub(r"^postgresql(\+[a-z0-9_]+)?://", "postgresql+psycopg2://", _database_url)
    SQLALCHEMY_DATABASE_URI = _database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    # Segredo do endpoint de webhook (Stripe Dashboard > Developers > Webhooks,
    # ou o segredo de stripe listen para testes). O webhook recusa pedidos
    # enquanto este segredo não estiver configurado.
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    # Quando nao ha chave Stripe configurada, o checkout corre em "modo demo":
    # cria a encomenda como paga sem ligar a nenhum processador de pagamentos.
    # Isto permite testar o fluxo todo no PyCharm sem precisares de uma conta Stripe.
    PAYMENTS_LIVE = bool(STRIPE_SECRET_KEY)

    DOMAIN = os.environ.get("DOMAIN", "http://127.0.0.1:5000")

    # Cookies de sessao (login do /admin, carrinho): "Secure" faz o browser
    # so os enviar por HTTPS, e "SameSite=Lax" evita que sejam enviados a
    # partir de pedidos vindos de outros sites. So ligamos o "Secure" quando
    # o DOMAIN e mesmo https:// -- em localhost (desenvolvimento, sem
    # HTTPS) isso fazia o login de admin nunca funcionar, porque o browser
    # recusa-se a enviar cookies "Secure" por HTTP.
    SESSION_COOKIE_SECURE = DOMAIN.startswith("https://")
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Password simples para entrares em /admin/orders e veres o que foi
    # vendido. Enquanto isto estiver vazio, a area de admin fica desligada
    # (ninguem consegue entrar).
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

    # Segunda password, so para a equipa do check-in a entrada -- da acesso
    # so ao scanner de bilhetes (/checkin), NAO da acesso a receita, lista
    # de encomendas, export Excel nem emissao de bilhetes de cortesia (ver
    # admin_required em blueprints/admin.py). Deixa em branco para
    # desligar (nesse caso so a ADMIN_PASSWORD funciona, como antes).
    CHECKIN_PASSWORD = os.environ.get("CHECKIN_PASSWORD", "")

    # Bloqueio temporário do site inteiro. A palavra-passe é configurada
    # exclusivamente no ambiente; vazia desliga o bloqueio.
    SITE_LOCK_USERNAME = os.environ.get("SITE_LOCK_USERNAME", "himdub")
    SITE_LOCK_PASSWORD = os.environ.get("SITE_LOCK_PASSWORD", "")

    # Envio do email com os bilhetes/QR code (via Resend -- resend.com).
    # Sem RESEND_API_KEY, os bilhetes continuam a ser gerados na compra,
    # mas o email nao e enviado a serio (modo demo, tal como o Stripe).
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
    RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "bilhetes@himdubfestival.com")
    EMAILS_LIVE = bool(RESEND_API_KEY)
    EMAIL_WORKER_ENABLED = PRODUCTION and os.environ.get("EMAIL_WORKER_ENABLED", "true").lower() == "true"

    FESTIVAL_DATES = "23 a 29 de agosto de 2027"
    FESTIVAL_LOCATION = "Praia Fluvial de Rapoula do Côa, Sabugal (Guarda)"

    # CDN para os ficheiros "pesados" (videos de fundo + musica de fundo,
    # via Bunny.net) -- feito para poupar bandwidth do Render, que cobra
    # por GB transferido. Vazio (por defeito) = esses ficheiros continuam a
    # vir diretamente do Render, tal como sempre foi. Definido (ex:
    # "https://himdubfestival.b-cdn.net") = passam a vir do Bunny, que
    # "puxa" ao Render na primeira vez que cada ficheiro e pedido e a
    # partir dai serve do cache dele, sem voltar a incomodar o Render --
    # ver cdn_url() em app.py. Se alguma vez o Bunny tiver um problema,
    # basta apagar esta variavel de ambiente no Render (sem mexer em
    # codigo) para os ficheiros voltarem a vir do Render na hora.
    CDN_BASE_URL = os.environ.get("CDN_BASE_URL", "").rstrip("/")

    # Pagina "brevemente" que bloqueia o site INTEIRO enquanto as vendas
    # ainda nao abriram (pedido do Joao para o lancamento) -- mostra uma
    # pagina informativa simples (ver coming_soon.py) em vez do site
    # normal, para ninguem entrar antes da hora combinada. Ao contrario de
    # um bloqueio por horario automatico, isto e sempre MANUAL: quando
    # chegar a hora de abrir as vendas, muda-se esta variavel de ambiente
    # no Render para "off" (sem precisar de novo deploy) e o site fica
    # acessivel a todos. Contam como "ligado": "on", "1", "true" (nao
    # importa maiusculas/minusculas) -- qualquer outro valor, incluindo
    # vazio, e "desligado".
    COMING_SOON_MODE = os.environ.get("COMING_SOON_MODE", "on").strip().lower() in ("on", "1", "true")


if Config.PRODUCTION:
    for _required in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "RESEND_API_KEY"):
        if not getattr(Config, _required):
            raise RuntimeError(f"{_required} must be configured in production.")
    if len(Config.SECRET_KEY) < 32:
        raise RuntimeError("SECRET_KEY must have at least 32 characters in production.")
    if not Config.DOMAIN.startswith("https://"):
        raise RuntimeError("DOMAIN must use HTTPS in production.")
    if not Config.SQLALCHEMY_DATABASE_URI.startswith("postgresql"):
        raise RuntimeError("Production requires PostgreSQL.")
