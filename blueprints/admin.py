"""Área de administração simples: ver as encomendas e o que foi vendido.

Não é um sistema de utilizadores a sério -- são só DUAS passwords
partilhadas (ADMIN_PASSWORD e CHECKIN_PASSWORD, no .env), cada uma com o
seu nível de acesso (ver ROLE_ADMIN/ROLE_CHECKIN, abaixo):

  - ADMIN_PASSWORD entra com acesso total: encomendas, receita, export
    Excel, emitir bilhetes de cortesia, e também o check-in.
  - CHECKIN_PASSWORD entra só no scanner de bilhetes (/checkin) -- não vê
    receita nem encomendas, nem consegue exportar Excel ou emitir
    bilhetes de cortesia (ver admin_required, abaixo, que bloqueia essas
    páginas para quem entrou só com esta password).

Se ambas estiverem vazias no .env, a área de admin fica toda desligada
(login recusa sempre)."""

import hmac
import io
import re
from datetime import datetime
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash,
    current_app, send_file, abort,
)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import selectinload
from security import local_redirect
from auth import session_valid, authenticate, check_login_throttle
from sqlalchemy import or_, func
from extensions import db
from models import Order, OrderItem, Product, Ticket, AuditEvent, EmailDelivery, encode_ticket_names
from i18n import DEFAULT_LANGUAGE
from festival_time import to_local_time, local_day_bounds_utc
from batches import (
    real_sales_launch_at, activate_real_sales_launch,
    real_remaining_in_current_tier, current_tier, TICKET_BATCHES, LOW_STOCK_ALERT_THRESHOLD,
)
# Reaproveita a mesma logica que o checkout normal usa para uma encomenda
# paga: gerar os Tickets/QR e despachar o email em background (ver
# blueprints/shop.py) -- assim um bilhete emitido aqui fica identico, em
# tudo, a um bilhete comprado a serio (mesmo PDF, mesmo email, mesmo
# check-in a entrada), so que sem passar pelo Stripe.
from blueprints.shop import (
    _finalize_paid_order, EMAIL_RE, _tier_batch_labels,
    COMPANION_NAME_SLUG, KIDS_NAMES_SLUG,
)
# Mesma funcao usada pelo checkout normal e pelo resend_ticket.py (script de
# terminal) -- aqui e so a versao "com botao", para nao ser preciso abrir
# um terminal so para reenviar um bilhete a um cliente.
from emailing import send_ticket_email, _group_tickets_by_recipient
from ticketing import _new_ticket_code

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# Os dois níveis de acesso possíveis (ver session["admin_role"], preenchido
# no login()) -- ROLE_ADMIN pode tudo, ROLE_CHECKIN só o scanner.
ROLE_ADMIN = "admin"
ROLE_CHECKIN = "checkin"


def login_required(view):
    """Só exige sessão iniciada (qualquer uma das duas passwords) -- usado
    nas páginas que a equipa do check-in também pode ver (ver
    blueprints/checkin.py). Para páginas só do ADMIN_PASSWORD, ver
    admin_required, abaixo."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session_valid():
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Como login_required, mas só deixa passar quem entrou com a
    ADMIN_PASSWORD (não a CHECKIN_PASSWORD) -- usado nas páginas com
    receita/encomendas/export/emissão de bilhetes de cortesia, que a
    equipa do check-in não deve conseguir ver nem usar."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session_valid():
            return redirect(url_for("admin.login", next=request.path))
        if session.get("admin_role") != ROLE_ADMIN:
            flash("Essa página não está disponível para o login de check-in.", "error")
            return redirect(url_for("checkin.scan"))
        return view(*args, **kwargs)
    return wrapped


@admin_bp.route("/")
def index():
    """/admin sozinho (sem /login ou /orders a seguir) não tinha nenhuma
    rota própria -- dava 404. Isto faz "/admin" ir sempre parar ao
    dashboard certo consoante o nível de acesso da sessão atual (ou ao
    login, se ainda não houver sessão nenhuma -- os próprios
    login_required/admin_required de cada rota tratam disso)."""
    if session.get("admin_role") == ROLE_CHECKIN:
        return redirect(url_for("checkin.scan"))
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    admin_password = current_app.config.get("ADMIN_PASSWORD", "")
    checkin_password = current_app.config.get("CHECKIN_PASSWORD", "")

    if request.method == "POST":
        submitted = request.form.get("password", "")
        email = request.form.get("email", "").strip().lower()[:180]
        check_login_throttle(email or "legacy")
        if email and authenticate(email, submitted):
            target = url_for("checkin.scan") if session.get("admin_role") == ROLE_CHECKIN else local_redirect(request.args.get("next"), url_for("admin.dashboard"))
            return redirect(target)
        if not current_app.config.get("ALLOW_LEGACY_LOGIN") or email:
            flash("Verifica o email, a palavra-passe e o código do autenticador.", "error")
            return render_template("admin/login.html"), 401

        # hmac.compare_digest em vez de "==" -- uma comparação normal de
        # strings pode parar assim que encontra a primeira letra errada,
        # o que (em teoria) deixa escapar, pelo tempo de resposta, quantas
        # letras do inicio da password alguém acertou. compare_digest
        # demora sempre o mesmo tempo, não dá essa pista.
        if not admin_password and not checkin_password:
            flash("A área de administração ainda não está configurada (falta ADMIN_PASSWORD no .env).", "error")
        elif admin_password and hmac.compare_digest(submitted, admin_password):
            session["is_admin"] = True
            session["admin_role"] = ROLE_ADMIN
            session["admin_name"] = "Administração"
            flash("Sessão de administrador iniciada.", "success")
            return redirect(local_redirect(request.args.get("next"), url_for("admin.orders")))
        # A password de check-in nunca segue o "next" -- quem entra só com
        # ela não tem acesso a mais nada além do scanner (ver
        # admin_required), por isso ir sempre direto para lá evita cair
        # logo a seguir no aviso "página não disponível" de admin_required.
        elif checkin_password and hmac.compare_digest(submitted, checkin_password):
            session["is_admin"] = True
            session["admin_role"] = ROLE_CHECKIN
            session["admin_name"] = "Equipa de Check-in"
            flash("Sessão de check-in iniciada.", "success")
            return redirect(url_for("checkin.scan"))
        else:
            flash("Password incorreta.", "error")

    return render_template("admin/login.html")


@admin_bp.route("/logout")
def logout():
    session.clear()
    flash("Sessão terminada.", "success")
    return redirect(url_for("admin.login"))


@admin_bp.route("/orders")
@admin_required
def orders():
    status_filter = request.args.get("status", "")
    search_query = request.args.get("q", "").strip()

    query = Order.query.options(selectinload(Order.items), selectinload(Order.tickets)).order_by(Order.created_at.desc(), Order.id.desc())
    if status_filter:
        query = query.filter_by(status=status_filter)
    if search_query:
        # ilike funciona tanto em Postgres (producao) como em SQLite (dev
        # local) -- o SQLAlchemy trata da traducao para cada um. O join com
        # Ticket e so para dar para pesquisar pelo codigo alfanumerico do
        # QR (ex: cliente liga a dizer "o meu codigo e C4ABBA108D") -- o
        # distinct() evita que uma encomenda com varios bilhetes apareca
        # duplicada nos resultados.
        like = f"%{search_query}%"
        query = query.outerjoin(Ticket, Ticket.order_id == Order.id).filter(
            or_(Order.name.ilike(like), Order.email.ilike(like), Ticket.code.ilike(like))
        ).distinct()
    page = query.paginate(page=request.args.get("page", 1, type=int), per_page=25, error_out=False)
    all_orders = page.items

    # Os cartoes-resumo no topo (receita, encomendas pagas, etc.) funcionam
    # normalmente (mostram tudo o que ha na base de dados, testes incluidos)
    # ATE se carregar no botao "Ativar contagem de vendas reais" -- o Joao
    # quer continuar a ver os numeros reais (incluindo testes) enquanto
    # ainda esta a testar o checkout, e so quer que ISSO zere no momento em
    # que carregar no botao, nao antes. A partir desse clique, os cartoes
    # passam a contar so o que for criado dai em diante (mesmo corte que o
    # real_sold_count() do sistema de lotes usa, ver batches.py). A lista de
    # encomendas mais abaixo (`orders=`) mostra sempre TUDO, em qualquer dos
    # casos, para nao perder o historico/gestao (reenviar bilhete, etc.).
    launch_at = real_sales_launch_at()
    stats_ids = query.with_entities(Order.id).order_by(None)
    if launch_at is not None:
        stats_ids = stats_ids.filter(Order.created_at >= launch_at)
    ids = stats_ids.distinct()
    totals = {status: (count, cents or 0) for status, count, cents in
              db.session.query(Order.status, func.count(Order.id), func.sum(Order.total_cents))
              .filter(Order.id.in_(ids)).group_by(Order.status).all()}
    paid_statuses = ("paid", "demo-paid", "comp")
    paid_count = sum(totals.get(s, (0, 0))[0] for s in paid_statuses)
    pending_count = sum(totals.get(s, (0, 0))[0] for s in ("pending", "processing"))
    comp_count = totals.get("comp", (0, 0))[0]
    revenue = sum(totals.get(s, (0, 0))[1] for s in paid_statuses) / 100
    item_totals = (db.session.query(OrderItem.product_name, OrderItem.variant, func.sum(OrderItem.quantity))
                   .join(Order).filter(Order.id.in_(ids), Order.status.in_(paid_statuses))
                   .group_by(OrderItem.product_name, OrderItem.variant).all())
    tickets_sold = {name + (f" ({variant})" if variant else ""): quantity for name, variant, quantity in item_totals}
    ticket_counts = (db.session.query(func.count(Ticket.id), func.count(Ticket.checked_in_at))
                     .join(Order).filter(Order.id.in_(ids), Order.status.in_(paid_statuses)).one())
    total_tickets, checked_in_count = ticket_counts

    # Aviso de stock baixo (ver batches.py real_remaining_in_current_tier)
    # -- mostrado sempre que for verdade, independentemente de já termos
    # mandado o email ou não (o email só vai uma vez por lote, mas este
    # aviso no admin fica visível enquanto a condição se mantiver).
    low_stock_warnings = []
    for slug in TICKET_BATCHES:
        remaining = real_remaining_in_current_tier(slug)
        if remaining is not None and 0 <= remaining <= LOW_STOCK_ALERT_THRESHOLD:
            tier_idx = current_tier(slug)[0]
            low_stock_warnings.append({
                "remaining": remaining,
                "label": _tier_batch_labels().get(tier_idx, f"Lote {tier_idx}"),
            })

    return render_template(
        "admin/orders.html",
        orders=all_orders,
        revenue=revenue,
        paid_count=paid_count,
        pending_count=pending_count,
        comp_count=comp_count,
        tickets_sold=sorted(tickets_sold.items(), key=lambda pair: -pair[1]),
        status_filter=status_filter,
        search_query=search_query,
        total_tickets=total_tickets,
        pagination=page,
        checked_in_count=checked_in_count,
        real_sales_launch_at=launch_at,
        low_stock_warnings=low_stock_warnings,
    )


@admin_bp.route("/activate-real-sales", methods=["POST"])
@admin_required
def activate_real_sales():
    """Liga, de vez, a contagem de "vendas reais" usada para decidir quando
    o Early Bird fecha e o 2nd batch abre (ver batches.py). Só deve ser
    carregado UMA VEZ, exatamente no momento em que o site passar a usar o
    domínio final himdubfestival.com -- tudo o que já foi vendido até esse
    clique (mesmo compras a sério, feitas para testar, no domínio
    provisório) fica de fora da contagem, porque real_sold_count() só conta
    encomendas com created_at a partir deste instante (ver batches.py).

    Ação protegida por password extra (para além de já estar atrás do
    admin_required) porque é irreversível na prática -- pedido explicito do
    João depois de ter feito 2 compras a sério para testar o checkout."""
    admin_password = current_app.config.get("ADMIN_PASSWORD", "")
    submitted = request.form.get("confirm_password", "")
    if not admin_password or not hmac.compare_digest(submitted, admin_password):
        flash("Password incorreta -- a contagem de vendas reais NÃO foi ativada.", "error")
    elif real_sales_launch_at() is not None:
        flash("A contagem de vendas reais já estava ativada -- não fiz nada.", "error")
    else:
        when = activate_real_sales_launch()
        flash(
            "Contagem de vendas reais ativada agora, "
            f"{to_local_time(when):%d/%m/%Y às %H:%M} (hora de Portugal). "
            "A partir deste momento, só bilhetes pagos a sério no Stripe (a partir de agora, "
            "excluindo tudo o que já foi vendido até este clique) contam para o 2nd batch.",
            "success",
        )
    return redirect(url_for("admin.orders"))


@admin_bp.route("/orders/<int:order_id>/resend-ticket", methods=["POST"])
@admin_required
def resend_ticket(order_id):
    """Reenvia o email de confirmação (com o(s) bilhete(s)/QR em anexo) de
    uma encomenda já existente -- é a mesma lógica do resend_ticket.py (o
    script de terminal), só que com um botão na página de encomendas, para
    não seres tu a abrir um terminal sempre que um cliente diz que não
    recebeu o email."""
    order = Order.query.filter_by(id=order_id).with_for_update().first_or_404()
    nonce = request.form.get("resend_key", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", nonce):
        abort(400)
    if order.status not in ("paid", "comp") or not current_app.config.get("EMAILS_LIVE"):
        flash("A encomenda não tem um envio disponível.", "error")
    else:
        from outbox import enqueue_order_emails
        enqueue_order_emails(order, event_id=nonce)
        db.session.add(AuditEvent(actor=session.get("admin_name", "staff"), action="email-resend", target=str(order.id)))
        db.session.commit()
        flash("O reenvio foi colocado na fila. Podes acompanhar o estado na fila de emails.", "success")
    return redirect(url_for("admin.orders"))


@admin_bp.route("/email-queue")
@admin_required
def email_queue():
    page = EmailDelivery.query.order_by(EmailDelivery.available_at.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=50, error_out=False)
    return render_template("admin/email_queue.html", pagination=page)


@admin_bp.route("/orders/<int:order_id>/edit-name", methods=["POST"])
@admin_required
def edit_order_name(order_id):
    """Corrige o nome e/ou o email do comprador de uma encomenda ja emitida
    (ex: o cliente escreveu o nome errado no checkout, o bilhete foi
    transferido para outra pessoa, ou o email tinha um erro de escrita) --
    pedido explicito do Joao.

    Para alem de mudar o nome/email, substitui o `code` de cada Ticket da
    encomenda por um novo: o PDF/QR ja enviado por email fica com um
    codigo que deixa de existir na base de dados, por isso o check-in
    recusa-o automaticamente (ve blueprints/checkin.py -- "not_found")
    sem precisar de nenhuma logica extra de "bilhete anulado". O codigo
    antigo fica guardado em `previous_codes`, so como rasto para suporte.
    Isto tambem se aplica so a mudar o email: se o cliente escreveu o email
    errado, o email antigo pode ter chegado a outra pessoa com um QR valido,
    por isso o bilhete tem de ser invalidado da mesma forma.

    Quando a encomenda tem mais do que um bilhete "normal" (ver
    _needs_ticket_names em blueprints/shop.py -- tudo exceto Acessibilidade/
    Família, que continuam com o seu próprio acompanhante/lista de crianças
    e não entram aqui), o formulário também traz um nome por bilhete
    (`ticket_name_<id do Ticket>`) -- pedido explícito do João, para poder
    corrigir/atribuir nomes diferentes num pedido de vários bilhetes iguais
    (ex: a cliente Raquel comprou 2 bilhetes e os dois saíram com o mesmo
    nome). Atualiza `Ticket.holder_name` de cada um e mantém
    `OrderItem.ticket_names` em sincronia (mesma codificação usada no
    checkout, ver models.py) para o caso de algo voltar a ler esse campo.

    Da mesma forma, o formulário também traz um email por bilhete
    (`ticket_email_<id do Ticket>`) -- pedido explícito do João, para dar
    para reencaminhar cada bilhete a sério para o email de quem o vai usar,
    em vez de todos irem sempre para `order.email`. Ao contrário do nome,
    este campo é OPCIONAL: vazio continua a significar "este bilhete vai
    junto no email para order.email", como sempre foi -- só quem tiver um
    email próprio preenchido aqui é que passa a receber o seu bilhete à
    parte (ver send_ticket_email em emailing.py, que agrupa os bilhetes por
    destinatário efetivo antes de enviar). Atualiza `Ticket.holder_email` e
    mantém `OrderItem.ticket_emails` em sincronia, tal como para os nomes.

    Nao envia email sozinho -- fica a proposito um passo à parte, para o
    Joao poder rever antes de carregar em "Reenviar bilhete" (que ja envia
    para o `order.email` atual, portanto para o email novo depois de
    guardares aqui) e so aí o cliente receber o bilhete corrigido."""
    order = Order.query.filter_by(id=order_id).with_for_update().first_or_404()
    new_name = request.form.get("new_name", "").strip()
    new_email = request.form.get("new_email", "").strip()

    # Bilhetes "normais" desta encomenda, agrupados por OrderItem (mantendo
    # a ordem em que foram criados -- ver generate_tickets_for_order em
    # ticketing.py -- para o índice bater certo com OrderItem.ticket_names).
    editable_tickets = [
        t for t in sorted(order.tickets, key=lambda t: t.id)
        if t.product_slug not in (COMPANION_NAME_SLUG, KIDS_NAMES_SLUG)
    ]
    ticket_name_values = {}
    ticket_email_values = {}
    missing_ticket_name = False
    invalid_ticket_email = None
    for ticket in editable_tickets:
        value = request.form.get(f"ticket_name_{ticket.id}", "").strip()[:120]
        ticket_name_values[ticket.id] = value
        if not value:
            missing_ticket_name = True

        email_value = request.form.get(f"ticket_email_{ticket.id}", "").strip()[:180]
        # Vazio é válido (ver docstring, acima) -- só valida o formato
        # quando o João de facto escreveu alguma coisa neste campo.
        if email_value and not EMAIL_RE.match(email_value):
            invalid_ticket_email = email_value
        ticket_email_values[ticket.id] = email_value

    if not new_name or not new_email:
        flash("Indica o nome e o email corretos antes de guardar.", "error")
    elif not EMAIL_RE.match(new_email):
        flash(f"O email \"{new_email}\" não parece válido -- confirma e tenta outra vez.", "error")
    elif not order.tickets:
        flash(f"A encomenda #{order.id} ainda não tem bilhetes emitidos -- não há nada para editar.", "error")
    elif missing_ticket_name:
        flash("Indica o nome de todos os bilhetes antes de guardar.", "error")
    elif invalid_ticket_email:
        flash(f"O email \"{invalid_ticket_email}\" de um dos bilhetes não parece válido -- confirma e tenta outra vez.", "error")
    else:
        old_name = order.name
        old_email = order.email
        order.name = new_name
        order.email = new_email

        names_changed = 0
        emails_changed = 0
        changed_ids = set()
        by_item_names = {}
        by_item_emails = {}
        for ticket in editable_tickets:
            new_holder_name = ticket_name_values[ticket.id]
            if new_holder_name != (ticket.holder_name or ""):
                names_changed += 1
                changed_ids.add(ticket.id)
            ticket.holder_name = new_holder_name
            by_item_names.setdefault(ticket.order_item_id, []).append(new_holder_name)

            new_holder_email = ticket_email_values[ticket.id]
            if new_holder_email != (ticket.holder_email or ""):
                emails_changed += 1
                changed_ids.add(ticket.id)
            ticket.holder_email = new_holder_email
            by_item_emails.setdefault(ticket.order_item_id, []).append(new_holder_email)

        for item_id, names in by_item_names.items():
            item = OrderItem.query.get(item_id)
            if item:
                item.ticket_names = encode_ticket_names(names)
        for item_id, emails in by_item_emails.items():
            item = OrderItem.query.get(item_id)
            if item:
                item.ticket_emails = encode_ticket_names(emails)

        for ticket in order.tickets:
            if old_name != new_name or (old_email != new_email and not ticket.holder_email):
                changed_ids.add(ticket.id)
            if ticket.id not in changed_ids:
                continue
            old_code = ticket.code
            new_code = _new_ticket_code()
            while Ticket.query.filter_by(code=new_code).first():  # colisão (muitíssimo raro)
                new_code = _new_ticket_code()
            ticket.previous_codes = f"{ticket.previous_codes},{old_code}" if ticket.previous_codes else old_code
            ticket.code = new_code
        db.session.add(AuditEvent(actor=session.get("admin_name", "staff"), action=request.endpoint or "admin-mutation", target=request.path[:80]))
        if changed_ids and current_app.config.get("EMAILS_LIVE"):
            from outbox import enqueue_order_emails
            order.email_sent = False
            enqueue_order_emails(order)
        db.session.commit()

        changes = []
        if old_name != new_name:
            changes.append(f"nome de \"{old_name}\" para \"{new_name}\"")
        if old_email != new_email:
            changes.append(f"email de \"{old_email}\" para \"{new_email}\"")
        if names_changed:
            changes.append(f"{names_changed} nome{'s' if names_changed != 1 else ''} de bilhete atualizado{'s' if names_changed != 1 else ''}")
        if emails_changed:
            changes.append(f"{emails_changed} email{'s' if emails_changed != 1 else ''} de bilhete atualizado{'s' if emails_changed != 1 else ''}")
        changed_text = " e ".join(changes) if changes else "dados confirmados (sem alterações)"

        flash(
            f"Encomenda #{order.id} corrigida -- {changed_text}. "
            + ("Os bilhetes alterados foram substituídos; o reenvio fica na fila automática." if changed_ids else "Nenhum código de bilhete foi alterado."),
            "success",
        )

    return redirect(url_for(
        "admin.orders",
        status=request.form.get("status") or None,
        q=request.form.get("q") or None,
    ))


def _write_sheet(ws, title, headers, rows):
    """Preenche uma folha do Excel com um cabeçalho a negrito (fixo, com
    autofilter) e ajusta a largura das colunas ao conteúdo -- reaproveitado
    pelas 3 folhas do export (ver export_excel, abaixo)."""
    ws.title = title
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2C3E36", end_color="2C3E36", fill_type="solid")

    ws.append(headers)
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"

    for row in rows:
        ws.append(row)
        for cell, value in zip(ws[ws.max_row], row):
            if isinstance(value, str):
                cell.data_type = "s"

    # Largura de coluna proporcional ao maior valor dessa coluna (com um
    # limite, para uma célula com texto muito longo -- ex: um email -- não
    # esticar a coluna toda a página).
    for col_index, header in enumerate(headers, start=1):
        longest = len(str(header))
        for row in rows:
            value = row[col_index - 1]
            longest = max(longest, len(str(value)) if value is not None else 0)
        ws.column_dimensions[get_column_letter(col_index)].width = min(longest + 2, 45)


@admin_bp.route("/export")
@admin_required
def export_excel():
    """Exporta encomendas, itens e bilhetes para um ficheiro Excel (.xlsx),
    para relatórios/contabilidade fora do site. Inclui encomendas de todos
    os estados (pagas, pendentes, cortesia, canceladas) com uma coluna
    "Estado" -- fica mais fácil filtrar/aplicar tabela dinâmica no Excel do
    que decidir aqui o que incluir ou não.

    Sem `start`/`end` (ex: botão "Exportar tudo") exporta tudo, como
    sempre. Com `start`/`end` (datas "YYYY-MM-DD", do date picker no
    admin) exporta só as encomendas criadas nesse intervalo de dias
    (inclusive dos dois extremos, na hora de Portugal -- ver
    local_day_bounds_utc em festival_time.py)."""
    start_str = request.args.get("start", "").strip()
    end_str = request.args.get("end", "").strip()

    query = Order.query.options(selectinload(Order.items), selectinload(Order.tickets)).order_by(Order.created_at.desc(), Order.id.desc())
    date_range_label = ""
    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else None
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else None
    except ValueError:
        # Datas ilegíveis (ex: URL editada à mão) -- ignora em vez de dar
        # erro 500, o pior que acontece é exportar tudo.
        start_date = end_date = None

    if start_date and end_date and start_date > end_date:
        start_date, end_date = end_date, start_date  # trocadas -- inverte em vez de dar 0 resultados

    if start_date:
        query = query.filter(Order.created_at >= local_day_bounds_utc(start_date)[0])
    if end_date:
        query = query.filter(Order.created_at < local_day_bounds_utc(end_date)[1])
    if start_date or end_date:
        date_range_label = f"-{start_date or 'inicio'}-a-{end_date or 'hoje'}"

    all_orders = query.all()
    # Legenda de cada lote do Full Pass (1=Early Bird, 2=2nd batch, ...) --
    # mesma fonte usada na página de bilhetes (ver blueprints/shop.py), para
    # não haver duas versões da mesma legenda espalhadas pelo código.
    tier_labels = _tier_batch_labels()

    wb = Workbook()

    orders_rows = [
        [
            order.id,
            to_local_time(order.created_at).strftime("%d/%m/%Y %H:%M") if order.created_at else "",
            order.name,
            order.email,
            order.nif,
            order.country,
            order.lang_code,
            order.status,
            # Tipo(s) de bilhete e lote(s) desta encomenda -- para não ser
            # preciso ir à folha "Itens" só para saber o que foi vendido
            # (pedido explícito do João).
            ", ".join(
                f"{item.quantity}× {item.product_name}" + (f" ({item.variant})" if item.variant else "")
                for item in order.items
            ),
            ", ".join(dict.fromkeys(
                tier_labels.get(item.batch_tier, f"Lote {item.batch_tier}")
                for item in order.items if item.batch_tier
            )),
            order.total,
            # Códigos QR de todos os bilhetes gerados por esta encomenda --
            # assim dá para ver, sem ir à folha "Bilhetes", que QR(s) uma
            # venda em concreto gerou (pedido explícito do João).
            ", ".join(ticket.code for ticket in order.tickets),
            "Sim" if order.email_sent else "Não",
            order.stripe_session_id,
        ]
        for order in all_orders
    ]
    _write_sheet(
        wb.active,
        "Encomendas",
        ["ID", "Data", "Nome", "Email", "NIF", "País", "Idioma", "Estado", "Tipo(s) de Bilhete", "Lote(s)", "Total (€)", "Códigos dos Bilhetes (QR)", "Email enviado", "Stripe Session ID"],
        orders_rows,
    )

    items_rows = [
        [
            item.order_id,
            to_local_time(order.created_at).strftime("%d/%m/%Y %H:%M") if order.created_at else "",
            order.status,
            item.product_name,
            item.variant,
            item.quantity,
            item.price_cents / 100,
            item.subtotal,
            item.batch_tier,
            # Só preenchido para bilhetes de Acessibilidade/Família (ver
            # OrderItem em models.py e checkout() em blueprints/shop.py).
            item.companion_name,
            item.formatted_kids_names,
        ]
        for order in all_orders
        for item in order.items
    ]
    _write_sheet(
        wb.create_sheet(),
        "Itens",
        ["ID Encomenda", "Data Encomenda", "Estado Encomenda", "Produto", "Variante", "Quantidade", "Preço unit. (€)", "Subtotal (€)", "Lote", "Acompanhante", "Crianças"],
        items_rows,
    )

    tickets_rows = [
        [
            ticket.code,
            ticket.order_id,
            order.name,
            order.email,
            ticket.product_name,
            ticket.variant,
            ticket.batch_tier,
            ticket.holder_name,
            ticket.companion_name,
            ticket.formatted_kids_names,
            ticket.wristband_code,
            to_local_time(ticket.checked_in_at).strftime("%d/%m/%Y %H:%M") if ticket.checked_in_at else "",
            ticket.checked_in_by,
        ]
        for order in all_orders
        for ticket in order.tickets
    ]
    _write_sheet(
        wb.create_sheet(),
        "Bilhetes",
        ["Código", "ID Encomenda", "Nome Comprador", "Email Comprador", "Produto", "Variante", "Lote", "Nome no Bilhete", "Acompanhante", "Crianças", "Pulseira", "Check-in em", "Check-in por"],
        tickets_rows,
    )

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"himdub-vendas{date_range_label}-{to_local_time(datetime.utcnow()):%Y-%m-%d-%H%M}.xlsx"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@admin_bp.route("/issue-ticket", methods=["GET", "POST"])
@admin_required
def issue_ticket():
    """Emitir um bilhete sem passar pelo Stripe -- para convidados VIP,
    embaixadores, giveaways, ofertas, etc. Cria uma Order normal (status
    "comp", total 0€) e reaproveita a mesma geracao de bilhete/QR + envio
    de email que uma compra a serio usa (ver _finalize_paid_order em
    blueprints/shop.py), para o resultado ser identico a um bilhete
    comprado (mesmo PDF, mesmo email, mesmo check-in a entrada)."""
    # Todos os produtos da categoria "bilhete" -- incluindo os inativos
    # (is_active=False), porque os bilhetes de cortesia (VIP, embaixadores)
    # sao propositadamente escondidos da loja publica mas continuam
    # disponiveis para seres tu a emiti-los aqui.
    ticket_products = (
        Product.query.filter_by(category="ticket")
        .order_by(Product.sort_order)
        .all()
    )

    form_name = ""
    form_email = ""
    form_quantity = "1"

    if request.method == "POST":
        form_name = request.form.get("name", "").strip()
        form_email = request.form.get("email", "").strip()
        form_quantity = request.form.get("quantity", "1").strip()
        product_id = request.form.get("product_id", "")

        try:
            quantity = max(1, min(20, int(form_quantity)))
        except ValueError:
            quantity = 1

        product = None
        if product_id.isdigit():
            product = Product.query.get(int(product_id))

        errors = []
        if not form_name:
            errors.append("Indica o nome de quem recebe o bilhete.")
        if not form_email or not EMAIL_RE.match(form_email):
            errors.append("Indica um email válido.")
        if not product:
            errors.append("Escolhe o tipo de bilhete.")

        if errors:
            for message in errors:
                flash(message, "error")
            return render_template(
                "admin/issue_ticket.html",
                products=ticket_products,
                form_name=form_name,
                form_email=form_email,
                form_quantity=form_quantity,
            )

        order = Order(
            name=form_name,
            email=form_email,
            country="",
            lang_code=DEFAULT_LANGUAGE,
            status="comp",
            total_cents=0,
        )
        order.items.append(OrderItem(
            product_id=product.id,
            product_name=product.localized_name,
            quantity=quantity,
            price_cents=0,
        ))
        db.session.add(order)
        db.session.add(AuditEvent(actor=session.get("admin_name", "staff"), action=request.endpoint or "admin-mutation", target=request.path[:80]))
        db.session.flush()

        _finalize_paid_order(order)

        flash(
            f"Bilhete emitido: {quantity}× {product.localized_name} para {form_name} "
            f"({form_email}). O email com o bilhete vai a caminho.",
            "success",
        )
        return redirect(url_for("admin.issue_ticket"))

    return render_template(
        "admin/issue_ticket.html",
        products=ticket_products,
        form_name=form_name,
        form_email=form_email,
        form_quantity=form_quantity,
    )


from admin_invites import init_invites
from admin_reports import init_reports
from cms import init_cms
init_invites(admin_bp, admin_required)
init_reports(admin_bp, admin_required, _write_sheet)
init_cms(admin_bp, admin_required)

from newsletter import init_newsletter
from blueprints.main import main_bp
init_newsletter(main_bp, admin_bp, admin_required)

from transfers import init_transfers
init_transfers(main_bp, admin_bp, admin_required)

from ticket_sales import init_ticket_sales
init_ticket_sales(admin_bp, admin_required)

from customer_portal import init_customer_admin
init_customer_admin(admin_bp, admin_required)
