"""Email recovery for existing staff; never bypasses MFA or creates accounts."""
from datetime import datetime, timedelta
import hashlib
import re
import secrets

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash
from auth import check_login_throttle, totp_counter
from extensions import db
from models import AdminUser, AdminPasswordReset, AuditEvent
from website_jobs import enqueue


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def init_password_recovery(bp):
    @bp.route('/forgot-password', methods=['GET', 'POST'])
    def forgot_password():
        sent = False
        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()
            if len(email) > 180 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                abort(400, description='Indica um endereço de email válido.')
            check_login_throttle('staff-recovery:' + email)
            now = datetime.utcnow()
            user = AdminUser.query.filter_by(email=email, active=True).with_for_update().first()
            if user:
                recent = AdminPasswordReset.query.filter(
                    AdminPasswordReset.user_id == user.id,
                    AdminPasswordReset.created_at > now - timedelta(minutes=1),
                ).first()
                if not recent:
                    raw = secrets.token_urlsafe(32)
                    db.session.add(AdminPasswordReset(
                        token_hash=digest(raw), user_id=user.id,
                        session_version=user.session_version,
                        expires_at=now + timedelta(minutes=20),
                    ))
                    link = current_app.config['DOMAIN'].rstrip('/') + url_for('admin.reset_password', token=raw)
                    enqueue('email', {
                        'from': current_app.config['RESEND_FROM_EMAIL'], 'to': [email],
                        'subject': 'Repor palavra-passe — HIM Dub Festival',
                        'html': render_template('emails/staff_password_reset.html', link=link),
                        'text': 'Define uma nova palavra-passe: ' + link + '\nLink privado, válido durante 20 minutos e uma única utilização. O autenticador mantém-se necessário. Se não pediste a alteração, ignora este email.',
                    }, 'staff-reset:' + digest(raw))
            db.session.commit()
            sent = True
        return render_template('admin/forgot_password.html', sent=sent)

    @bp.route('/reset-password/<token>', methods=['GET', 'POST'])
    def reset_password(token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            abort(404)
        identifier = digest(token)
        item = db.session.get(AdminPasswordReset, identifier)
        if not item:
            return render_template('admin/reset_password.html', invalid=True), 410
        # Lock the user before its token on every mutation. Concurrent links for
        # this user must not both succeed, even when they are different tokens.
        user_query = AdminUser.query.filter_by(id=item.user_id)
        user = (user_query.populate_existing().with_for_update().first()
                if request.method == 'POST' else user_query.first())
        if request.method == 'POST':
            item = AdminPasswordReset.query.filter_by(token_hash=identifier).populate_existing().with_for_update().first()
        if (not user or not user.active or not item or item.used_at
                or item.expires_at <= datetime.utcnow() or item.session_version != user.session_version):
            db.session.rollback()
            return render_template('admin/reset_password.html', invalid=True), 410
        error = None
        if request.method == 'POST':
            check_login_throttle('staff-reset-submit:' + identifier)
            # Throttling commits its own transaction; reacquire both locks and
            # revalidate after it, before touching credentials.
            user = user_query.populate_existing().with_for_update().first()
            item = AdminPasswordReset.query.filter_by(token_hash=identifier).populate_existing().with_for_update().first()
            if (not user or not user.active or not item or item.used_at
                    or item.expires_at <= datetime.utcnow() or item.session_version != user.session_version):
                db.session.rollback()
                return render_template('admin/reset_password.html', invalid=True), 410
            password = request.form.get('password', '')
            confirm = request.form.get('password_confirm', '')
            counter = None
            if len(password) < 14 or len(password) > 256 or password != confirm:
                error = 'Usa entre 14 e 256 caracteres e repete a mesma palavra-passe nos dois campos.'
            elif user.totp_secret:
                counter = totp_counter(user.totp_secret, request.form.get('totp', '').strip())
                if counter is None or counter <= user.totp_last_counter:
                    error = 'Verifica o código do autenticador. Se acabaste de o usar, espera pelo próximo código.'
            elif current_app.config.get('PRODUCTION') and user.role == 'admin':
                error = 'Esta conta precisa de recuperação assistida do autenticador. Contacta a organização.'
            if error:
                needs_mfa = bool(user.totp_secret)
                db.session.rollback()
                return render_template('admin/reset_password.html', error=error, needs_mfa=needs_mfa), 400
            user.password_hash = generate_password_hash(password)
            user.session_version += 1
            if counter is not None:
                user.totp_last_counter = counter
            item.used_at = datetime.utcnow()
            db.session.add(AuditEvent(actor=user.email, action='password_reset', target=str(user.id)))
            enqueue('email', {
                'from': current_app.config['RESEND_FROM_EMAIL'], 'to': [user.email],
                'subject': 'Palavra-passe alterada — HIM Dub Festival',
                'html': render_template('emails/staff_password_changed.html'),
                'text': 'A palavra-passe da tua conta de equipa foi alterada. As sessões anteriores terminaram e o autenticador foi mantido. Se não foste tu, contacta imediatamente a organização.',
            }, 'staff-reset-notice:' + identifier)
            db.session.commit()
            session.clear()
            flash('Palavra-passe actualizada. Entra com a nova palavra-passe e um novo código do autenticador, se configurado.', 'success')
            return redirect(url_for('admin.login'))
        return render_template('admin/reset_password.html', needs_mfa=bool(user.totp_secret))
