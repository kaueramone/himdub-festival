"""Single-use invitations with browser-bound, server-side MFA enrolment."""

import base64
import hashlib
import io
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

import qrcode
from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_file,
)
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash
from auth import check_login_throttle, totp_counter
from extensions import db
from models import AdminUser, AdminInvite, AdminEnrollment, AuditEvent


def init_invites(bp, admin_required):
    def invite_for(token, lock=False):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            abort(404)
        query = AdminInvite.query.filter_by(
            token_hash=hashlib.sha256(token.encode()).hexdigest()
        )
        row = (query.with_for_update() if lock else query).first()
        if not row or row.revoked or row.used_at or row.expires_at <= datetime.utcnow():
            abort(
                410,
                description="Este convite expirou, foi utilizado ou foi revogado. Pede um novo convite à organização.",
            )
        if row.role not in ("admin", "checkin"):
            abort(410, description="Categoria de acesso inválida. Pede um novo convite.")
        issuer = (
            AdminUser.query.filter_by(id=row.created_by).with_for_update().first()
            if lock
            else db.session.get(AdminUser, row.created_by)
        )
        if not issuer or not issuer.active or issuer.role != "admin":
            abort(410, description="Este convite já não está disponível.")
        return row

    @bp.route("/team", methods=["GET", "POST"])
    @admin_required
    def team():
        link = None
        if request.method == "POST":
            if not session.get("user_id"):
                abort(403)
            email = request.form.get("email", "").strip().lower()
            role = request.form.get("role", "admin")
            if role not in ("admin", "checkin"):
                abort(400, description="Escolhe Administrador ou Staff — Check-in.")
            if email and (
                len(email) > 180 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
            ):
                abort(400, description="Indica um email válido ou deixa o campo vazio.")
            check_login_throttle("invite-create:" + str(session["user_id"]))
            token = secrets.token_urlsafe(32)
            invitation = AdminInvite(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                created_by=session["user_id"],
                email=email,
                role=role,
                expires_at=datetime.utcnow() + timedelta(hours=24),
            )
            db.session.add(invitation)
            db.session.add(
                AuditEvent(actor=session.get("admin_name"), action="invite_created")
            )
            db.session.commit()
            link = current_app.config["DOMAIN"].rstrip("/") + url_for(
                "admin.join", token=token
            )
        return render_template(
            "admin/team.html",
            invite_link=link,
            invites=AdminInvite.query.order_by(AdminInvite.created_at.desc())
            .limit(50)
            .all(),
            staff=AdminUser.query.order_by(AdminUser.email).all(),
            now=datetime.utcnow(),
        )

    @bp.route("/team/invites/<int:invite_id>/revoke", methods=["POST"])
    @admin_required
    def revoke_invite(invite_id):
        invite = db.session.get(AdminInvite, invite_id)
        if not invite:
            abort(404)
        invite.revoked = True
        db.session.add(
            AuditEvent(
                actor=session.get("admin_name"),
                action="invite_revoked",
                target=str(invite.id),
            )
        )
        db.session.commit()
        flash("Convite revogado.", "success")
        return redirect(url_for("admin.team"))

    @bp.route("/team/users/<int:user_id>/revoke", methods=["POST"])
    @admin_required
    def revoke_staff(user_id):
        if user_id == session.get("user_id"):
            abort(400, description="Não podes desativar o teu próprio acesso.")
        active_admins = (
            AdminUser.query.filter_by(role="admin", active=True)
            .order_by(AdminUser.id)
            .populate_existing()
            .with_for_update()
            .all()
        )
        if session.get("user_id") not in {user.id for user in active_admins}:
            abort(403)
        user = db.session.get(AdminUser, user_id)
        if not user:
            abort(404)
        user.active = False
        user.session_version += 1
        db.session.add(
            AuditEvent(
                actor=session.get("admin_name"),
                action="staff_revoked",
                target=str(user.id),
            )
        )
        db.session.commit()
        flash(
            "Acesso desativado. As sessões e os convites dessa pessoa deixam de funcionar.",
            "success",
        )
        return redirect(url_for("admin.team"))

    @bp.route("/join/<token>", methods=["GET", "POST"])
    def join(token):
        invite = invite_for(token, lock=request.method == "POST")
        key = "enrollment_" + str(invite.id)
        enrollment = db.session.get(AdminEnrollment, session.get(key, ""))
        if enrollment and (
            enrollment.invite_id != invite.id
            or enrollment.expires_at <= datetime.utcnow()
        ):
            enrollment = None
        error = None
        if request.method == "POST":
            check_login_throttle("invite:" + str(invite.id))
            if request.form.get("step") == "verify":
                # Reacquire the invitation lock after throttle's independent commit.
                invite = invite_for(token, lock=True)
                if not enrollment:
                    abort(
                        400,
                        description="A configuração expirou. Volta a preencher os teus dados.",
                    )
                counter = totp_counter(
                    enrollment.totp_secret, request.form.get("totp", "").strip()
                )
                if counter is None:
                    error = "Código incorreto. Usa o código atual da aplicação de autenticação."
                elif AdminUser.query.filter_by(email=enrollment.email).first():
                    error = "Já existe uma conta com este email. Entra com o acesso existente."
                else:
                    user = AdminUser(
                        email=enrollment.email,
                        password_hash=enrollment.password_hash,
                        role=invite.role,
                        totp_secret=enrollment.totp_secret,
                        totp_last_counter=counter,
                    )
                    invite.used_at = datetime.utcnow()
                    db.session.add(user)
                    db.session.add(
                        AuditEvent(
                            actor=enrollment.email,
                            action="invite_accepted",
                            target=str(invite.id),
                        )
                    )
                    db.session.delete(enrollment)
                    try:
                        db.session.commit()
                    except IntegrityError:
                        db.session.rollback()
                        abort(409, description="O convite ou email já foi utilizado.")
                    session.clear()
                    session.permanent = True
                    session.update(
                        is_admin=True,
                        user_id=user.id,
                        session_version=user.session_version,
                        admin_role=user.role,
                        admin_name=user.email,
                    )
                    return redirect(url_for("checkin.scan" if user.role == "checkin" else "admin.dashboard"))
            else:
                email = request.form.get("email", "").strip().lower()
                password = request.form.get("password", "")
                if len(email) > 180 or not re.fullmatch(
                    r"[^\s@]+@[^\s@]+\.[^\s@]+", email
                ):
                    error = "Indica um email válido."
                elif invite.email and email != invite.email:
                    error = "Usa o email para o qual este convite foi criado."
                elif len(password) < 14 or len(password) > 256:
                    error = "Escolhe uma palavra-passe entre 14 e 256 caracteres."
                elif password != request.form.get("password_confirm"):
                    error = "As palavras-passe não coincidem."
                elif AdminUser.query.filter_by(email=email).first():
                    error = "Já existe uma conta com este email."
                else:
                    if enrollment:
                        db.session.delete(enrollment)
                    enrollment = AdminEnrollment(
                        id=secrets.token_urlsafe(32),
                        invite_id=invite.id,
                        email=email,
                        password_hash=generate_password_hash(password),
                        totp_secret=base64.b32encode(secrets.token_bytes(20)).decode(),
                        expires_at=min(
                            invite.expires_at, datetime.utcnow() + timedelta(minutes=15)
                        ),
                    )
                    db.session.add(enrollment)
                    db.session.commit()
                    session[key] = enrollment.id
                    return redirect(url_for("admin.join", token=token))
        response = current_app.make_response(
            render_template(
                "admin/join.html",
                invite=invite,
                enrollment=enrollment,
                token=token,
                error=error,
            )
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @bp.route("/join/<token>/qr")
    def invite_qr(token):
        invite = invite_for(token)
        enrollment = db.session.get(
            AdminEnrollment, session.get("enrollment_" + str(invite.id), "")
        )
        if (
            not enrollment
            or enrollment.invite_id != invite.id
            or enrollment.expires_at <= datetime.utcnow()
        ):
            abort(404)
        uri = (
            "otpauth://totp/HIM%20Dub:"
            + quote(enrollment.email)
            + "?secret="
            + enrollment.totp_secret
            + "&issuer=HIM%20Dub"
        )
        image = io.BytesIO()
        qrcode.make(uri).save(image, format="PNG")
        image.seek(0)
        response = send_file(image, mimetype="image/png", max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
