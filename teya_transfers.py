"""Teya holder changes: signed webhook plus authoritative API verification."""
from datetime import datetime, timedelta
import uuid
from urllib.parse import urlsplit
from flask import abort, current_app, redirect
from extensions import db
from models import TransferCheckout, TicketTransfer
import teya_gateway as gateway


def create_session(payment):
    gateway.matching_environment(payment)
    transfer = db.session.get(TicketTransfer, payment.transfer_id)
    origin = current_app.config['DOMAIN'].rstrip('/')
    data = gateway.api('POST', '/v2/checkout/sessions', {
        'store_id': payment.store_id, 'type': 'SALE',
        'amount': {'value': transfer.fee_cents, 'currency': 'EUR'},
        'merchant_reference': payment.reference,
        'line_items': [{'description': 'HIM Dub — troca de titular de 1 bilhete',
                        'quantity': 1, 'unit_price': transfer.fee_cents}],
        'language': 'pt-PT', 'success_url': origin + '/ticket-transfer/return',
        'cancel_url': origin + '/ticket-transfer/return',
        'failure_url': origin + '/ticket-transfer/return',
        'post_success_payment': 'REDIRECT', 'save_payment_method': False,
    }, payment.id)
    try:
        session_id = str(uuid.UUID(data['session_id']))
        target = urlsplit(data['session_url'])
        if (target.scheme != 'https' or target.hostname != gateway.environment_hosts()[2]
                or target.username or target.password or target.port not in (None, 443) or target.fragment):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise gateway.TeyaUnavailable('Resposta de checkout inválida') from None
    from transfers import lock_transfer
    lock_transfer(transfer.id)
    payment = TransferCheckout.query.filter_by(id=payment.id).populate_existing().with_for_update().one()
    if payment.session_id and payment.session_id != session_id:
        raise gateway.TeyaUnavailable('Sessão inesperada')
    payment.session_id, payment.session_url = session_id, data['session_url']
    if payment.status == 'preparing':
        payment.status = 'active'
        payment.next_check_at = datetime.utcnow() + timedelta(minutes=2)
    db.session.commit()
    return payment.session_url


def start_transfer_checkout(transfer):
    if not gateway.ready():
        abort(503, description='Este meio de pagamento ainda não está disponível.')
    from transfers import lock_transfer
    transfer, _ticket = lock_transfer(transfer.id)
    if transfer.status != 'pending_payment' or transfer.payment_provider != 'teya':
        abort(409, description='Este pedido já não está disponível para pagamento.')
    payment = TransferCheckout.query.filter_by(transfer_id=transfer.id).first()
    if not payment:
        identifier = str(uuid.uuid4())
        payment = TransferCheckout(id=identifier, transfer_id=transfer.id,
            environment=current_app.config['TEYA_ENVIRONMENT'], store_id=current_app.config['TEYA_STORE_ID'],
            reference='HIM-TRANSFER-' + identifier)
        db.session.add(payment)
        db.session.commit()
    if payment.status not in ('preparing', 'active', 'processing'):
        abort(409, description='Este pagamento já foi concluído. Consulta o email de confirmação.')
    try:
        gateway.matching_environment(payment)
        return redirect(payment.session_url or create_session(payment), code=303)
    except gateway.TeyaUnavailable:
        db.session.rollback()
        payment = db.session.get(TransferCheckout, payment.id)
        payment.last_error = 'Pagamento por confirmar; não repetir a cobrança'
        payment.next_check_at = datetime.utcnow() + timedelta(minutes=2)
        db.session.commit()
        abort(503, description='Não foi possível confirmar a abertura do pagamento. O bilhete mantém-se inalterado. Consulta este mesmo link mais tarde antes de repetir.')


def reconcile(identifier, webhook=None):
    payment = db.session.get(TransferCheckout, identifier)
    if not payment:
        return
    gateway.matching_environment(payment)
    try:
        session_id = str(uuid.UUID(payment.session_id or (webhook or {}).get('session_id')))
    except (ValueError, TypeError, AttributeError):
        raise gateway.TeyaUnavailable('Sessão por reconciliar') from None
    data = gateway.api('GET', '/v2/checkout/sessions/' + session_id)
    from transfers import lock_transfer, complete_transfer
    transfer, ticket = lock_transfer(payment.transfer_id)
    payment = TransferCheckout.query.filter_by(id=identifier).populate_existing().with_for_update().one()
    amount = data.get('amount') or {}
    if (transfer.payment_provider != 'teya' or data.get('session_id') != session_id
            or data.get('merchant_reference') != payment.reference or data.get('type') != 'SALE'
            or amount.get('currency') != 'EUR' or type(amount.get('value')) is not int
            or amount['value'] != transfer.fee_cents or (payment.session_id and payment.session_id != session_id)):
        raise ValueError('Teya transfer mismatch')
    if webhook and (webhook.get('session_id') != session_id
            or webhook.get('merchant_reference') != payment.reference or webhook.get('amount') != amount
            or webhook.get('status') != 'SUCCESS' or webhook.get('transaction_id') != data.get('transaction_id')):
        raise ValueError('Teya transfer webhook mismatch')
    payment.session_id = session_id
    state, status = data.get('session_status'), data.get('payment_status')
    if state == 'COMPLETED' and status == 'SUCCESS':
        transaction = data.get('transaction_id')
        if not isinstance(transaction, str) or not transaction or len(transaction) > 100:
            raise ValueError('Missing Teya transaction')
        if payment.transaction_id and payment.transaction_id != transaction:
            raise ValueError('Unexpected Teya transaction')
        payment.transaction_id, payment.status, payment.last_error = transaction, 'paid', ''
        if transfer.status in ('completed', 'refund_pending', 'refunded'):
            db.session.commit()
            return
        complete_transfer(transfer, ticket)
    elif state == 'EXPIRED' or (state == 'COMPLETED' and status == 'FAILED'):
        if payment.status != 'paid' and transfer.status == 'pending_payment':
            payment.status = 'expired' if state == 'EXPIRED' else 'failed'
            payment.last_error = ''
            transfer.status, transfer.active_ticket_id = 'cancelled', None
        db.session.commit()
    else:
        payment.last_error = ''
        payment.next_check_at = datetime.utcnow() + timedelta(minutes=2)
        db.session.commit()


def refresh_transfer(transfer):
    payment = TransferCheckout.query.filter_by(transfer_id=transfer.id).first()
    if payment and payment.session_id and transfer.status == 'pending_payment':
        try:
            reconcile(payment.id)
        except (gateway.TeyaUnavailable, ValueError):
            db.session.rollback()


def reconcile_pending(app, limit=3):
    with app.app_context():
        if not gateway.configured():
            return
        now = datetime.utcnow()
        ids = [p.id for p in TransferCheckout.query.filter(
            TransferCheckout.status.in_(('preparing', 'active', 'processing')),
            TransferCheckout.next_check_at <= now).order_by(TransferCheckout.next_check_at).limit(limit)]
        for identifier in ids:
            claimed = TransferCheckout.query.filter_by(id=identifier).filter(TransferCheckout.next_check_at <= now).update(
                {'next_check_at': now + timedelta(minutes=3)}, synchronize_session=False)
            db.session.commit()
            if not claimed:
                continue
            try:
                payment = db.session.get(TransferCheckout, identifier)
                if not payment.session_id:
                    create_session(payment)
                reconcile(identifier)
            except Exception:
                db.session.rollback()
                payment = db.session.get(TransferCheckout, identifier)
                payment.last_error = 'Pagamento por reconciliar; bilhete preservado'
                db.session.commit()
                app.logger.warning('Teya holder change requires reconciliation')
