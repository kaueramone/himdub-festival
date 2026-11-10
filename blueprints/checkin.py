"""Check-in à entrada do festival: ler o QR de um bilhete e associá-lo ao
número da pulseira cashless (GoFun) entregue ao cliente.

Protegido pelo mesmo login simples de /admin (é a ferramenta do porteiro,
não algo público).
"""

from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort, current_app
from extensions import db
from models import Ticket, Order, AuditEvent
import re
from sqlalchemy.exc import IntegrityError
from blueprints.admin import login_required
from festival_time import to_local_time

checkin_bp = Blueprint("checkin", __name__, url_prefix="/checkin")


@checkin_bp.route("/scan")
@login_required
def scan():
    """Página com o leitor de QR pela câmara do telemóvel (ver
    static/js/checkin.js). Ao ler um código com sucesso, navega para
    /checkin/<code> automaticamente.
    """
    code = request.args.get("code", "").strip().upper()
    if code:
        if re.fullmatch(r"[A-Z0-9]{4,60}", code):
            return redirect(url_for("checkin.ticket_detail", code=code))
        flash("Introduz apenas o código do bilhete, sem espaços ou ligações.", "error")
    return render_template("checkin/scan.html")


@checkin_bp.route("/<code>", methods=["GET", "POST"])
@login_required
def ticket_detail(code):
    ticket = Ticket.query.filter_by(code=code.upper()).first()

    if not ticket:
        return render_template("checkin/not_found.html", code=code), 404

    # Lock in the same order as payment fulfillment.
    order = Order.query.filter_by(id=ticket.order_id).with_for_update().populate_existing().one()
    ticket = Ticket.query.filter_by(id=ticket.id).with_for_update().populate_existing().one()
    allowed = {"paid", "comp"}
    if not current_app.config.get("PRODUCTION"):
        allowed.add("demo-paid")
    if order.status not in allowed:
        return render_template("checkin/invalid.html"), 403

    if request.method == "POST":
        wristband_code = request.form.get("wristband_code", "").strip()
        # O QR do bilhete não muda depois de usado -- não há nada que
        # impeça alguém de o fotografar/reenviar e tentar fazer check-in
        # outra vez com outra pulseira. Por isso, se o bilhete já tem
        # checked_in_at, o registo só é atualizado se vier explicitamente
        # "confirm_override=yes" (ver templates/checkin/ticket.html -- fica
        # escondido dentro de um <details> fechado + checkbox obrigatória,
        # para o porteiro nunca corrigir uma pulseira por engano só por
        # estar a despachar bilhetes depressa à entrada).
        already_checked_in = ticket.is_checked_in
        confirm_override = request.form.get("confirm_override") == "yes"

        if confirm_override and (session.get("admin_role") != "admin" or not request.form.get("override_reason", "").strip()):
            abort(403, description="A correção exige um administrador e uma justificação.")
        if len(wristband_code) > 60:
            abort(400)
        if Ticket.query.filter(Ticket.wristband_code == wristband_code, Ticket.id != ticket.id).first():
            abort(409, description="Esta pulseira já está associada a outro bilhete.")
        if not wristband_code:
            flash("Indica o número da pulseira.", "error")
        elif already_checked_in and not confirm_override:
            flash(
                f"Este bilhete já tinha feito check-in às "
                f"{to_local_time(ticket.checked_in_at).strftime('%H:%M')}, associado à pulseira "
                f"{ticket.wristband_code}. Não foi alterado nada -- se for mesmo "
                f"preciso corrigir, pede a intervenção de um administrador.",
                "error",
            )
        else:
            ticket.wristband_code = wristband_code
            ticket.checked_in_at = datetime.utcnow()
            ticket.checked_in_by = session.get("admin_name", "porteiro")
            db.session.add(AuditEvent(actor=ticket.checked_in_by,
                action="checkin_corrected" if already_checked_in else "checkin_completed", target=str(ticket.id)))
            if already_checked_in:
                current_app.logger.warning("Check-in correction ticket=%s operator=%s reason=%s", ticket.id, ticket.checked_in_by, request.form.get("override_reason", "")[:200])
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                abort(409, description="Esta pulseira já está associada a outro bilhete.")
            flash(f"Bilhete associado à pulseira {wristband_code}.", "success")
            return redirect(url_for("checkin.ticket_detail", code=ticket.code))

    return render_template("checkin/ticket.html", ticket=ticket)
