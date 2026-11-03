"""Individual staff accounts, revocable sessions and login throttling."""
from datetime import datetime, timedelta
import hashlib
import secrets
import base64
import hmac
import struct
import time
import os
from urllib.parse import quote

import click
from flask import abort, current_app, request, session
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from models import AdminUser, AuditEvent, LoginThrottle

# Avoid a cheap early return for unknown email addresses.
_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32))


def session_valid():
    if not session.get("is_admin"):
        return False
    if session.get("user_id"):
        user = db.session.get(AdminUser, session["user_id"])
        if user and user.active and user.session_version == session.get("session_version"):
            session["admin_role"] = user.role
            session["admin_name"] = user.email
            return True
        session.clear()
        return False
    return current_app.config.get("ALLOW_LEGACY_LOGIN", False)


def check_login_throttle(identifier):
    now = datetime.utcnow()
    # Account and IP budgets are independent: neither rotating usernames nor IPs bypasses both.
    budgets = [("account:" + identifier.lower(), 5), ("ip:" + (request.remote_addr or "unknown"), 25)]
    for value, limit in budgets:
        key = hashlib.sha256((current_app.config["SECRET_KEY"] + value).encode()).hexdigest()
        row = LoginThrottle.query.filter_by(key=key).with_for_update().first()
        if not row:
            row = LoginThrottle(key=key, window_start=now, attempts=0)
            db.session.add(row)
            try:
                db.session.flush()
            except IntegrityError:
                db.session.rollback()
                row = LoginThrottle.query.filter_by(key=key).with_for_update().one()
        if now - row.window_start >= timedelta(minutes=15):
            row.window_start, row.attempts = now, 0
        if row.attempts >= limit:
            db.session.rollback()
            abort(429, description="Demasiadas tentativas. Tenta novamente dentro de 15 minutos.")
        row.attempts += 1
        db.session.commit()


def totp_counter(secret, code, timestamp=None):
    if not code.isdigit() or len(code) != 6:
        return None
    counter = int(time.time() if timestamp is None else timestamp) // 30
    key = base64.b32decode(secret)
    for candidate in (counter, counter - 1, counter + 1):
        digest = hmac.new(key, struct.pack(">Q", candidate), hashlib.sha1).digest()
        offset = digest[-1] & 15
        expected = f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000:06d}"
        if hmac.compare_digest(code, expected):
            return candidate
    return None


def authenticate(email, password):
    user = AdminUser.query.filter_by(email=email.strip().lower()).with_for_update().first()
    valid = check_password_hash(user.password_hash if user else _DUMMY_HASH, password)
    if user and user.active and valid:
        if user.totp_secret:
            counter = totp_counter(user.totp_secret, request.form.get("totp", "").strip())
            if counter is None or counter <= user.totp_last_counter:
                db.session.rollback()
                return False
            user.totp_last_counter = counter
        elif current_app.config.get("PRODUCTION") and user.role == "admin":
            db.session.rollback()
            return False
        session.clear()
        session.permanent = True
        session.update(is_admin=True, user_id=user.id, session_version=user.session_version,
                       admin_role=user.role, admin_name=user.email)
        db.session.add(AuditEvent(actor=user.email, action="login"))
        db.session.commit()
        return True
    return False


def init_auth(app):
    @app.cli.command("staff-bootstrap")
    def staff_bootstrap():
        """Provision the first staff account from temporary, private deployment variables."""
        if os.environ.get("STAFF_BOOTSTRAP_ROTATE_MFA", "").lower() == "true":
            email = os.environ.get("STAFF_BOOTSTRAP_EMAIL", "").strip().lower()
            secret = os.environ.get("STAFF_BOOTSTRAP_TOTP_SECRET", "")
            user = AdminUser.query.filter_by(email=email, role="admin", active=True).first()
            if not user or len(secret) != 32 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in secret):
                raise click.ClickException("Conta ou segredo de recuperação MFA inválido.")
            if not hmac.compare_digest(user.totp_secret or "", secret):
                user.totp_secret = secret
                user.totp_last_counter = -1
                user.session_version += 1
                db.session.add(AuditEvent(actor="deployment", action="mfa_reset", target=str(user.id)))
                db.session.commit()
            click.echo("MFA da conta indicada atualizado; as sessões anteriores foram revogadas.")
            return
        if AdminUser.query.count():
            click.echo("Já existem contas; não foi alterado nenhum acesso.")
            return
        email = os.environ.get("STAFF_BOOTSTRAP_EMAIL", "").strip().lower()
        password_hash = os.environ.get("STAFF_BOOTSTRAP_PASSWORD_HASH", "")
        secret = os.environ.get("STAFF_BOOTSTRAP_TOTP_SECRET", "")
        if ("@" not in email or len(email) > 180 or not password_hash.startswith("scrypt:")
                or len(secret) != 32 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in secret)):
            raise click.ClickException("Faltam os acessos privados de preparação da primeira conta.")
        db.session.add(AdminUser(email=email, role="admin", password_hash=password_hash, totp_secret=secret))
        db.session.commit()
        click.echo("Primeira conta administrativa preparada com MFA.")

    @app.cli.command("user-add")
    @click.argument("email")
    @click.option("--role", type=click.Choice(["admin", "checkin"]), default="admin")
    @click.option("--mfa/--no-mfa", default=True)
    @click.password_option()
    def user_add(email, role, mfa, password):
        """Create a staff account; never sends an email or prints the password."""
        email = email.strip().lower()
        if "@" not in email or len(email) > 180 or len(password) < 14:
            raise click.ClickException("Usa um email válido e uma palavra-passe com pelo menos 14 caracteres.")
        if AdminUser.query.filter_by(email=email).first():
            raise click.ClickException("A conta já existe.")
        if app.config.get("PRODUCTION") and role == "admin" and not mfa:
            raise click.ClickException("Administradores de produção precisam de autenticação de dois fatores.")
        secret = base64.b32encode(secrets.token_bytes(20)).decode() if mfa else ""
        db.session.add(AdminUser(email=email, role=role, password_hash=generate_password_hash(password), totp_secret=secret))
        db.session.commit()
        click.echo("Conta criada.")
        if secret:
            click.echo("Importa este URI na aplicação de autenticação. É um segredo; não o guardes no Git nem em relatórios:")
            click.echo(f"otpauth://totp/HIM%20Dub:{quote(email)}?secret={secret}&issuer=HIM%20Dub")

    @app.cli.command("user-revoke")
    @click.argument("email")
    def user_revoke(email):
        user = AdminUser.query.filter_by(email=email.strip().lower()).first()
        if not user:
            raise click.ClickException("Conta não encontrada.")
        user.active = False
        user.session_version += 1
        db.session.commit()
        click.echo("Conta revogada; as sessões deixam de ter acesso.")

    @app.cli.command("user-password")
    @click.argument("email")
    @click.password_option()
    def user_password(email, password):
        user = AdminUser.query.filter_by(email=email.strip().lower()).first()
        if not user or len(password) < 14:
            raise click.ClickException("Conta inválida ou palavra-passe com menos de 14 caracteres.")
        user.password_hash = generate_password_hash(password)
        user.session_version += 1
        db.session.commit()
        click.echo("Palavra-passe atualizada; as sessões anteriores foram revogadas.")
