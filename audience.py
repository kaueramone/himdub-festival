"""First-party, opt-in audience measurement. No IP, raw UA, URL or form storage."""
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from flask import Blueprint, current_app, request, session, g, jsonify
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import select
from extensions import db
from models import AudienceConsent, AudienceVisit, AudiencePage, AudienceOrder

audience_bp = Blueprint('audience', __name__, url_prefix='/audience')
VERSION = '2026-11-v1'
CONSENT_COOKIE = 'himdub_statistics'
VISITOR_COOKIE = 'himdub_audience'
VISIT_COOKIE = 'himdub_visit'
PAGES = {
    'main.home':'Início', 'main.festival':'O festival', 'main.lineup':'Lineup',
    'main.area_detail':'Palcos', 'main.participate':'Participate',
    'main.ambassadors':'Embaixadores', 'main.venue_map':'Mapa', 'main.faq':'FAQ',
    'main.partners':'Parceiros', 'main.sustainability':'Sustentabilidade',
    'main.contact':'Contactos', 'main.gallery':'Galeria', 'main.gallery_album':'Álbum',
    'main.legal_page':'Informações legais', 'shop.tickets':'Tickets',
    'shop.glamping':'The Experience', 'shop.product_detail':'Produto',
    'shop.merchandise':'Loja', 'shop.view_cart':'Carrinho', 'shop.checkout':'Checkout',
}
BOT = re.compile(r'bot|crawler|spider|headless|lighthouse|preview|monitor|python|curl|wget', re.I)
_cleanup_lock = threading.Lock()
_cleanup_at = 0


def serializer(purpose):
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='audience-'+purpose)


def cookie_value(name, purpose, age):
    try:
        value = serializer(purpose).loads(request.cookies.get(name, ''), max_age=age)
        return value if isinstance(value, str) else None
    except BadSignature:
        return None


def visitor_key():
    value = cookie_value(VISITOR_COOKIE, 'visitor', 90*86400)
    if not value:
        return None
    return hmac.new(current_app.config['SECRET_KEY'].encode(), value.encode(), hashlib.sha256).hexdigest()


def excluded():
    return bool(session.get('is_admin') or request.headers.get('Sec-GPC') == '1'
                or request.headers.get('DNT') == '1' or BOT.search(request.user_agent.string))


def choice():
    if excluded():
        return 'blocked'
    value = request.cookies.get(CONSENT_COOKIE, '')
    if value == 'no':
        return 'no'
    if value == 'yes':
        consent = db.session.get(AudienceConsent, visitor_key()) if visitor_key() else None
        if consent and not consent.revoked_at and consent.expires_at > datetime.utcnow():
            return 'yes'
    return ''


def country():
    reader = current_app.extensions.get('country_geolocation')
    try:
        address = ipaddress.ip_address(request.remote_addr)
        value = (reader.get(str(address)) or {}).get('country', {}).get('iso_code') if reader and address.is_global else ''
        return value if isinstance(value,str) and re.fullmatch('[A-Z]{2}',value) else ''
    except (ValueError, TypeError):
        return ''


def device_info():
    ua = request.user_agent.string.lower()
    device = 'Tablet' if any(x in ua for x in ('ipad','tablet')) or ('android' in ua and 'mobile' not in ua) else ('Telemóvel' if any(x in ua for x in ('mobile','iphone','android')) else 'Computador')
    browser = next((name for needle,name in [('edg/','Edge'),('samsungbrowser','Samsung Internet'),('opr/','Opera'),('firefox','Firefox'),('fxios','Firefox'),('chrome','Chrome'),('crios','Chrome'),('safari','Safari')] if needle in ua),'Outro')
    return device, browser


def origin_info(data):
    known = {'instagram':'Instagram','facebook':'Facebook','google':'Google','youtube':'YouTube',
             'newsletter':'Newsletter','email':'Newsletter','tiktok':'TikTok','bing':'Bing'}
    source = str(data.get('source') or '').lower()
    campaign = str(data.get('campaign') or '')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,59}',campaign) or re.search(r'\d{7}',campaign):
        campaign = ''
    if source:
        medium = str(data.get('medium') or '').lower()
        return known.get(source,'Outra campanha'), medium if medium in ('cpc','paid_social','email','organic','social','referral') else 'campaign', campaign
    raw = str(data.get('referrer') or '')[:1000]
    try:
        parsed = urlsplit(raw if '://' in raw else 'https://'+raw)
        host = (parsed.hostname or '').lower().removeprefix('www.')
        own = (urlsplit(current_app.config['DOMAIN']).hostname or '').lower().removeprefix('www.')
        if not host or host == own or parsed.username or parsed.password or not re.fullmatch(r'[a-z0-9.-]{3,100}',host):
            return 'Directo / desconhecido','direct',campaign
        try:
            ipaddress.ip_address(host)
            return 'Directo / desconhecido','direct',campaign
        except ValueError:
            pass
        for suffix,label in [('instagram.com','Instagram'),('facebook.com','Facebook'),('youtube.com','YouTube'),('tiktok.com','TikTok'),('bing.com','Bing')]:
            if host == suffix or host.endswith('.'+suffix):
                return label,'social' if label in ('Instagram','Facebook','YouTube','TikTok') else 'organic',campaign
        if re.fullmatch(r'(?:[a-z0-9-]+\.)?google\.(?:com|pt|fr|es|co\.uk|de|be)',host):
            return 'Google','organic',campaign
        # Keep a bounded referring host only. Never paths, queries or fragments.
        return host,'referral',campaign
    except ValueError:
        return 'Directo / desconhecido','direct',campaign


def current_visit():
    if excluded() or request.cookies.get(CONSENT_COOKIE) != 'yes':
        return None
    key = visitor_key()
    if not key:
        return None
    consent = AudienceConsent.query.filter_by(key=key).with_for_update().first()
    if not consent or consent.revoked_at or consent.expires_at <= datetime.utcnow():
        return None
    identifier = cookie_value(VISIT_COOKIE, 'visit', 1800)
    return AudienceVisit.query.filter_by(id=identifier, visitor_key=key).filter(
        AudienceVisit.last_seen >= datetime.utcnow()-timedelta(minutes=30)).with_for_update().first() if identifier else None


def record_cart(empty=False):
    """Successful cart mutations only; analytics failure must not break a cart."""
    try:
        with db.session.begin_nested():
            visit = current_visit()
            if visit:
                visit.cart_at = visit.cart_at or datetime.utcnow()
                visit.cart_cleared = empty
                visit.last_seen = datetime.utcnow()
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.warning('Audience cart measurement unavailable')


def link_order(order):
    """Called within the purchase transaction. A savepoint protects checkout."""
    try:
        with db.session.begin_nested():
            visit = current_visit()
            if visit and not db.session.get(AudienceOrder, order.id):
                visit.checkout_at = visit.checkout_at or datetime.utcnow()
                visit.cart_at = visit.cart_at or visit.checkout_at
                visit.cart_cleared = False
                visit.last_seen = datetime.utcnow()
                db.session.add(AudienceOrder(order_id=order.id, visit_id=visit.id))
    except Exception:
        current_app.logger.warning('Audience checkout attribution unavailable')


def clear_visits(key):
    ids = select(AudienceVisit.id).where(AudienceVisit.visitor_key == key)
    AudiencePage.query.filter(AudiencePage.visit_id.in_(ids)).delete(synchronize_session=False)
    AudienceOrder.query.filter(AudienceOrder.visit_id.in_(ids)).delete(synchronize_session=False)
    AudienceVisit.query.filter_by(visitor_key=key).delete(synchronize_session=False)


def cleanup(force=False):
    global _cleanup_at
    with _cleanup_lock:
        if not force and time.monotonic() < _cleanup_at:
            return
        now = datetime.utcnow()
        ids = select(AudienceVisit.id).where(AudienceVisit.last_seen < now-timedelta(days=90))
        AudiencePage.query.filter(AudiencePage.visit_id.in_(ids)).delete(synchronize_session=False)
        AudienceOrder.query.filter(AudienceOrder.visit_id.in_(ids)).delete(synchronize_session=False)
        AudienceVisit.query.filter(AudienceVisit.last_seen < now-timedelta(days=90)).delete(synchronize_session=False)
        # Expired/revoked consent evidence is minimised after the same period.
        old = select(AudienceConsent.key).where(AudienceConsent.expires_at < now)
        for key, in db.session.query(AudienceConsent.key).filter(AudienceConsent.key.in_(old)).all():
            clear_visits(key)
        AudienceConsent.query.filter(AudienceConsent.expires_at < now).delete(synchronize_session=False)
        _cleanup_at = time.monotonic()+3600


def set_cookie(response, name, value, age):
    response.set_cookie(name,value,max_age=age,secure=bool(current_app.config['PRODUCTION']),httponly=True,samesite='Lax')


@audience_bp.post('/consent')
def consent():
    value = request.form.get('choice')
    if value not in ('yes','no'):
        return {'error':'Escolha inválida'},400
    response = jsonify(choice=value)
    key = visitor_key()
    if value == 'yes':
        if excluded():
            return {'choice':'blocked'},200
        existing = AudienceConsent.query.filter_by(key=key).with_for_update().first() if key else None
        raw = cookie_value(VISITOR_COOKIE,'visitor',90*86400) if existing and not existing.revoked_at and existing.expires_at > datetime.utcnow() else secrets.token_urlsafe(32)
        key = hmac.new(current_app.config['SECRET_KEY'].encode(),raw.encode(),hashlib.sha256).hexdigest()
        if existing and existing.key == key:
            existing.expires_at = datetime.utcnow()+timedelta(days=90)
        else:
            db.session.add(AudienceConsent(key=key,expires_at=datetime.utcnow()+timedelta(days=90),version=VERSION))
        db.session.commit()
        set_cookie(response,VISITOR_COOKIE,serializer('visitor').dumps(raw),90*86400)
    else:
        row = AudienceConsent.query.filter_by(key=key).with_for_update().first() if key else None
        if row:
            row.revoked_at = datetime.utcnow()
            clear_visits(key)
            db.session.commit()
        response.delete_cookie(VISITOR_COOKIE)
        response.delete_cookie(VISIT_COOKIE)
    set_cookie(response,CONSENT_COOKIE,value,180*86400)
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@audience_bp.post('/collect')
def collect():
    if excluded() or request.cookies.get(CONSENT_COOKIE) != 'yes':
        return '',204
    try:
        data = request.get_json(silent=True) if request.is_json else json.loads(request.form.get('data','{}'))
        if not isinstance(data,dict) or set(data)-{'token','seconds','scroll','referrer','source','medium','campaign'}:
            return '',400
        grant = serializer('page').loads(data.get('token',''),max_age=7200)
        binding = hashlib.sha256(session.get('csrf_token','').encode()).hexdigest()
        if (not isinstance(grant,dict) or grant.get('page') not in PAGES
                or grant.get('binding') != binding or not re.fullmatch(r'[0-9a-f]{32}',grant.get('nonce',''))):
            return '',400
        if type(data.get('seconds',0)) is not int or type(data.get('scroll',0)) is not int:
            return '',400
        key = visitor_key()
        consent_row = AudienceConsent.query.filter_by(key=key).with_for_update().first() if key else None
        now = datetime.utcnow()
        if not consent_row or consent_row.revoked_at or consent_row.expires_at <= now:
            return '',204
        if not consent_row.event_window or consent_row.event_window < now-timedelta(hours=1):
            consent_row.event_window = now
            consent_row.event_count = 0
        if consent_row.event_count >= 1000:
            db.session.rollback()
            return '',429
        consent_row.event_count += 1
        visit = current_visit()
        if not visit:
            source,medium,campaign = origin_info(data)
            device,browser = device_info()
            visit = AudienceVisit(id=str(uuid.uuid4()),visitor_key=key,
                is_returning=AudienceVisit.query.filter_by(visitor_key=key).first() is not None,
                source=source,medium=medium,campaign=campaign,country=country(),device=device,browser=browser,
                language=grant.get('lang') if grant.get('lang') in ('pt','en','fr','es') else 'en',
                entry_page=grant['page'],exit_page=grant['page'],started_at=now,last_seen=now)
            db.session.add(visit)
            db.session.flush()
        page_id = hashlib.sha256((visit.id+grant['nonce']).encode()).hexdigest()
        page = db.session.get(AudiencePage,page_id)
        if not page:
            if visit.page_views >= 200:
                return '',204
            page = AudiencePage(id=page_id,visit_id=visit.id,page=grant['page'],started_at=now,active_seconds=0,scroll_percent=0)
            db.session.add(page)
            visit.page_views += 1
            visit.exit_page = grant['page']
        cap = min(1800,max(0,int((now-page.started_at).total_seconds())+2))
        seconds = max(page.active_seconds,min(cap,max(0,data.get('seconds',0))))
        visit.active_seconds += seconds-page.active_seconds
        page.active_seconds = seconds
        page.scroll_percent = max(page.scroll_percent,min(100,max(0,data.get('scroll',0))))
        visit.last_seen = now
        cleanup()
        db.session.commit()
        response = jsonify(ok=True)
        set_cookie(response,VISIT_COOKIE,serializer('visit').dumps(visit.id),1800)
        response.headers['Cache-Control'] = 'private, no-store'
        return response
    except (BadSignature, ValueError, TypeError, AttributeError):
        db.session.rollback()
        return '',400
    except Exception:
        db.session.rollback()
        current_app.logger.warning('Audience measurement unavailable')
        return '',204


def init_audience(app):
    @app.context_processor
    def context():
        from security import csrf_token
        from audience_copy import COPY
        page = request.endpoint if request.method == 'GET' and request.endpoint in PAGES else ''
        state = choice() if not request.path.startswith(('/admin','/checkin','/static')) else 'blocked'
        token = serializer('page').dumps({'page':page,'nonce':uuid.uuid4().hex,
            'binding':hashlib.sha256(csrf_token().encode()).hexdigest(),
            'lang':getattr(g,'lang_code','en')}) if page and state != 'blocked' else ''
        return {'audience_page_token':token,'audience_choice':state,'audience_page':page,
                'audience_copy':COPY.get(getattr(g,'lang_code','en'),COPY['en'])}

    @app.after_request
    def private_measurement(response):
        if request.endpoint in PAGES or request.blueprint == 'audience':
            response.headers['Cache-Control'] = 'private, no-store'
        return response

    @app.cli.command('audience-prune')
    def prune():
        cleanup(force=True)
        db.session.commit()
