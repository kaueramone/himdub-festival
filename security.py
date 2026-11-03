"""Request protections shared by browser forms and administration."""
import hmac
import secrets
from urllib.parse import urlsplit

from flask import abort, request, session, jsonify


def local_redirect(value, fallback):
    if not value or not value.startswith("/") or value.startswith("//"):
        return fallback
    parsed = urlsplit(value)
    if parsed.netloc or parsed.scheme or "\\" in value or any(ord(c) < 32 for c in value):
        return fallback
    return value


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def init_security(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["new_form_nonce"] = lambda: secrets.token_urlsafe(24)
    app.config["MAX_CONTENT_LENGTH"] = app.config.get("MAX_CONTENT_LENGTH") or 2 * 1024 * 1024

    def form_error(error):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ok=False, error=error.description), error.code
        return error
    for status in (400, 409, 429):
        app.register_error_handler(status, form_error)

    @app.before_request
    def protect_mutations():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return
        if request.path.startswith(('/admin/ticket-sales/', '/admin/content/edit', '/admin/content/original/edit', '/admin/appearance', '/admin/gallery/')):
            request.max_content_length = 16 * 1024 * 1024
        # This endpoint authenticates the original payload with Stripe's signature.
        if request.endpoint == "shop.stripe_webhook":
            return
        expected = session.get("csrf_token", "")
        provided = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not expected or not provided or not hmac.compare_digest(expected.encode(), provided.encode()):
            abort(400, description="Formulário expirado. Atualiza a página e tenta novamente.")

    @app.after_request
    def response_protections(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if request.path.startswith(("/admin", "/checkin", "/checkout")):
            response.headers["Cache-Control"] = "private, no-store"
        if request.path.startswith(('/newsletter/', '/ticket-transfer', '/account')):
            response.headers['Cache-Control']='private, no-store'
            response.headers['Referrer-Policy']='no-referrer'
        if '/admin/join/' in request.path or request.endpoint == 'admin.team':
            response.headers['Referrer-Policy'] = 'no-referrer'
        return response
