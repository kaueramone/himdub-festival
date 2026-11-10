"""Teya hosted checkout. No card data or bearer token reaches the browser."""
import base64
import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit
import requests
from flask import abort, current_app, redirect, request
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from extensions import db
from models import Order, PaymentCheckout, PaymentGatewayConfiguration

_tokens = {}
_token_lock = threading.Lock()
SCOPES = 'checkout/sessions/create checkout/sessions/id/get'


class TeyaUnavailable(Exception):
    """Only generic messages: never expose provider bodies or credentials."""


def provider():
    config = db.session.get(PaymentGatewayConfiguration, 1)
    return config.provider if config else 'stripe'


def configured():
    c = current_app.config
    return c.get('TEYA_ENVIRONMENT') in ('production', 'staging') and all(c.get(k) for k in (
        'TEYA_CLIENT_ID', 'TEYA_CLIENT_SECRET', 'TEYA_STORE_ID', 'TEYA_WEBHOOK_PUBLIC_KEY'))


def ready():
    return configured() and current_app.config.get('TEYA_CHECKOUT_READY', False) and (
        not current_app.config.get('PRODUCTION') or current_app.config['TEYA_ENVIRONMENT'] == 'production')


def environment_hosts():
    environment = current_app.config.get('TEYA_ENVIRONMENT')
    if environment not in ('production', 'staging'):
        raise TeyaUnavailable('Ambiente Teya inválido')
    suffix = 'com' if environment == 'production' else 'xyz'
    return f'https://id.teya.{suffix}', f'https://api.teya.{suffix}', f'checkout.teya.{suffix}'


def access_token(force=False):
    c = current_app.config
    issuer, _, _ = environment_hosts()
    # Never reuse a cached token after changing credentials or environment.
    key = hashlib.sha256((issuer+c.get('TEYA_CLIENT_ID','')+c.get('TEYA_CLIENT_SECRET','')).encode()).hexdigest()
    with _token_lock:
        cached = _tokens.get(key)
        if cached and cached[1] > time.monotonic() and not force:
            return cached[0]
        try:
            response = requests.post(issuer+'/oauth/v2/oauth-token', data={
                'grant_type':'client_credentials', 'client_id':c['TEYA_CLIENT_ID'],
                'client_secret':c['TEYA_CLIENT_SECRET'], 'scope':SCOPES,
            }, headers={'User-Agent':'HimDubFestival/1.0'}, timeout=(5,15), allow_redirects=False)
            if response.status_code != 200:
                raise TeyaUnavailable('Não foi possível autenticar na Teya')
            data = response.json()
            token, expires = data['access_token'], int(data['expires_in'])
            if not isinstance(token,str) or not token or expires <= 0:
                raise ValueError()
        except (requests.RequestException, ValueError, KeyError, TypeError):
            raise TeyaUnavailable('Não foi possível autenticar na Teya') from None
        _tokens[key] = (token,time.monotonic()+max(0,expires-60))
        return token


def api(method, path, payload=None, idempotency=None):
    _, origin, _ = environment_hosts()
    for attempt in range(2):
        headers = {'Authorization':'Bearer '+access_token(force=bool(attempt)),
                   'User-Agent':'HimDubFestival/1.0'}
        if idempotency:
            headers['Idempotency-Key'] = idempotency
        try:
            response = requests.request(method,origin+path,json=payload,headers=headers,
                                        timeout=(5,15),allow_redirects=False)
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code not in (200,201):
                raise TeyaUnavailable('Pedido Teya não confirmado')
            data = response.json()
            if not isinstance(data,dict):
                raise ValueError()
            return data
        except (requests.RequestException, ValueError):
            raise TeyaUnavailable('Pedido Teya não confirmado') from None
    raise TeyaUnavailable('Pedido Teya não confirmado')


def matching_environment(payment):
    if (payment.environment != current_app.config.get('TEYA_ENVIRONMENT')
            or payment.store_id != current_app.config.get('TEYA_STORE_ID')):
        raise TeyaUnavailable('A configuração não corresponde a este pagamento')


def create_session(payment):
    matching_environment(payment)
    order = db.session.get(Order,payment.order_id)
    from blueprints.shop import _order_token
    lines = [{'description':i.product_name+(f' ({i.variant})' if i.variant else ''),
              'quantity':i.quantity,'unit_price':i.price_cents} for i in order.items]
    fee = order.total_cents-sum(i.price_cents*i.quantity for i in order.items)
    if fee < 0:
        raise TeyaUnavailable('Total inválido')
    if fee:
        lines.append({'description':'Taxa de processamento','quantity':1,'unit_price':fee})
    origin = current_app.config['DOMAIN'].rstrip('/')
    data = api('POST','/v2/checkout/sessions', {
        'store_id':payment.store_id,'type':'SALE','amount':{'value':order.total_cents,'currency':'EUR'},
        'merchant_reference':payment.reference,'line_items':lines,
        'language':{'pt':'pt-PT','en':'en-GB','fr':'fr-FR'}.get(order.lang_code,'pt-PT'),
        'success_url':origin+f'/checkout/success/{order.id}?token={_order_token(order.id)}',
        'cancel_url':origin+'/checkout/cancel',
        'failure_url':origin+'/checkout/cancel','post_success_payment':'REDIRECT',
        'save_payment_method':False,
    }, payment.id)
    try:
        session_id = str(uuid.UUID(data['session_id']))
        target = urlsplit(data['session_url'])
        if (target.scheme!='https' or target.hostname!=environment_hosts()[2] or target.username
                or target.password or target.port not in (None,443) or target.fragment):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise TeyaUnavailable('Resposta de checkout Teya inválida') from None
    # A fast webhook may have fulfilled the order while this call was in flight.
    Order.query.filter_by(id=payment.order_id).populate_existing().with_for_update().one()
    payment = PaymentCheckout.query.filter_by(id=payment.id).populate_existing().with_for_update().one()
    if payment.session_id and payment.session_id != session_id:
        raise TeyaUnavailable('Sessão Teya inesperada')
    payment.session_id = session_id
    payment.session_url = data['session_url']
    if payment.status == 'preparing':
        payment.status = 'active'
        payment.next_check_at = datetime.utcnow()+timedelta(minutes=16)
    db.session.commit()
    return payment.session_url


def start_checkout(order):
    if not ready():
        abort(503,description='Este meio de pagamento ainda não está disponível. Tenta novamente mais tarde.')
    identifier = str(uuid.uuid4())
    payment = PaymentCheckout(id=identifier,order_id=order.id,environment=current_app.config['TEYA_ENVIRONMENT'],
        store_id=current_app.config['TEYA_STORE_ID'],reference=f'HIM-{order.id}-{identifier}')
    # Uncertain or processing Teya payments keep their stock until the API
    # confirms a terminal status. A browser cancellation never releases stock.
    order.status='processing'
    order.reservation_expires_at=None
    db.session.add(payment)
    db.session.commit()
    try:
        return redirect(create_session(payment),code=303)
    except TeyaUnavailable:
        db.session.rollback()
        payment=db.session.get(PaymentCheckout,identifier)
        payment.last_error='Criação de sessão por reconciliar'
        payment.next_check_at=datetime.utcnow()+timedelta(minutes=2)
        db.session.commit()
        abort(503,description='Não foi possível abrir o pagamento. Não foi emitido nenhum bilhete. Se concluíste um pagamento, aguarda a confirmação por email antes de repetir.')


def reconcile(payment_id, webhook=None):
    payment=db.session.get(PaymentCheckout,payment_id)
    if not payment:
        return
    matching_environment(payment)
    session_id=payment.session_id or (webhook or {}).get('session_id')
    try:
        session_id=str(uuid.UUID(session_id))
    except (ValueError,TypeError,AttributeError):
        raise TeyaUnavailable('Sessão Teya por reconciliar') from None
    data=api('GET','/v2/checkout/sessions/'+session_id)
    order=Order.query.filter_by(id=payment.order_id).populate_existing().with_for_update().one()
    payment=PaymentCheckout.query.filter_by(id=payment_id).populate_existing().with_for_update().one()
    amount=data.get('amount') or {}
    if (data.get('session_id')!=session_id or data.get('merchant_reference')!=payment.reference
            or data.get('type')!='SALE' or amount.get('currency')!='EUR'
            or type(amount.get('value')) is not int or amount['value']!=order.total_cents
            or (payment.session_id and payment.session_id!=session_id)):
        raise ValueError('Teya payment/order mismatch')
    payment.session_id=session_id
    if webhook and (webhook.get('session_id')!=session_id
            or webhook.get('merchant_reference')!=payment.reference
            or webhook.get('amount')!=amount or webhook.get('status')!='SUCCESS'
            or webhook.get('transaction_id')!=data.get('transaction_id')):
        raise ValueError('Teya webhook/session mismatch')
    state=data.get('session_status')
    status=data.get('payment_status')
    if state=='COMPLETED' and status=='SUCCESS':
        transaction=data.get('transaction_id')
        if not isinstance(transaction,str) or not transaction or len(transaction)>100:
            raise ValueError('Missing Teya transaction')
        if payment.transaction_id and payment.transaction_id!=transaction:
            raise ValueError('Unexpected Teya transaction')
        if order.status not in ('pending','processing','paid'):
            if order.status=='refunded':
                return
            raise ValueError('Teya paid order requires review')
        payment.transaction_id=transaction
        payment.status='paid'
        payment.last_error=''
        order.status='paid'
        from blueprints.shop import _finalize_paid_order
        _finalize_paid_order(order)
    elif state=='EXPIRED' or (state=='COMPLETED' and status=='FAILED'):
        if order.status in ('pending','processing'):
            order.status='cancelled'
            payment.status='expired' if state=='EXPIRED' else 'failed'
            payment.last_error=''
            db.session.commit()
    else:
        payment.last_error=''
        payment.next_check_at=datetime.utcnow()+timedelta(minutes=2)
        db.session.commit()


def reconcile_pending(app, limit=3):
    with app.app_context():
        if not configured():
            return
        now=datetime.utcnow()
        ids=[r.id for r in PaymentCheckout.query.filter(
            PaymentCheckout.status.in_(('preparing','active','processing')),
            PaymentCheckout.next_check_at<=now).order_by(PaymentCheckout.next_check_at).limit(limit).all()]
        for identifier in ids:
            claimed=PaymentCheckout.query.filter_by(id=identifier).filter(PaymentCheckout.next_check_at<=now).update(
                {'next_check_at':now+timedelta(minutes=3)},synchronize_session=False)
            db.session.commit()
            if not claimed:
                continue
            try:
                payment=db.session.get(PaymentCheckout,identifier)
                if not payment.session_id:
                    create_session(payment)  # Same durable idempotency key after timeouts.
                reconcile(identifier)
            except Exception:
                db.session.rollback()
                payment=db.session.get(PaymentCheckout,identifier)
                payment.last_error='Pagamento por reconciliar; reserva mantida'
                db.session.commit()
                app.logger.warning('Teya reconciliation deferred for checkout %s',identifier)


def init_teya(bp):
    @bp.post('/webhooks/teya')
    def teya_webhook():
        key=current_app.config.get('TEYA_WEBHOOK_PUBLIC_KEY','')
        if not key:
            return '',503
        raw=request.get_data()
        try:
            public_key=serialization.load_pem_public_key(key.encode())
            if not isinstance(public_key,rsa.RSAPublicKey):
                raise ValueError()
            signature=base64.b64decode(request.headers.get('x-teya-signature',''),validate=True)
            public_key.verify(signature,raw,padding.PKCS1v15(),hashes.SHA256())
            event=json.loads(raw)
            if not isinstance(event,dict):
                raise ValueError()
        except (ValueError,TypeError,InvalidSignature):
            return '',400
        if event.get('event')!='payment.succeeded.v1':
            return '',200
        data=event.get('data') or {}
        if not isinstance(data,dict):
            return '',400
        payment=PaymentCheckout.query.filter_by(reference=data.get('merchant_reference'),provider='teya').first()
        if not payment:
            return '',200  # Other payment links/terminal payments are not website orders.
        try:
            reconcile(payment.id,webhook=data)
        except ValueError:
            db.session.rollback()
            current_app.logger.warning('Teya payment/order mismatch rejected')
            return '',400
        except Exception:
            db.session.rollback()
            current_app.logger.warning('Teya confirmation deferred; retry required')
            return '',503
        return '',200
