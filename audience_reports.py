"""Cohort analytics and operational sales are deliberately separate measures."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from flask import abort, render_template, request
from sqlalchemy import func
from babel import Locale
from extensions import db
from festival_time import to_local_time, local_day_bounds_utc
from audience import PAGES
from models import AudienceVisit, AudiencePage, AudienceOrder, Order, PaymentCheckout


def window():
    today = to_local_time(datetime.utcnow()).date()
    try:
        days = int(request.args.get('days','30'))
        if days not in (7,30,90):
            raise ValueError()
        first = datetime.strptime(request.args['start'],'%Y-%m-%d').date() if request.args.get('start') else today-timedelta(days=days-1)
        last = datetime.strptime(request.args['end'],'%Y-%m-%d').date() if request.args.get('end') else today
        if first > last or last > today or first < today-timedelta(days=89):
            raise ValueError()
    except ValueError:
        abort(400,description='Escolhe um período válido dentro dos últimos 90 dias.')
    return first,last,local_day_bounds_utc(first)[0],local_day_bounds_utc(last)[1]


def report():
    first,last,start,end = window()
    now = datetime.utcnow()
    visits = AudienceVisit.query.filter(AudienceVisit.started_at >= start,AudienceVisit.started_at < end)
    ids = visits.with_entities(AudienceVisit.id).subquery()
    rows = visits.with_entities(AudienceVisit.id,AudienceVisit.visitor_key,AudienceVisit.started_at,
        AudienceVisit.last_seen,AudienceVisit.is_returning,AudienceVisit.source,AudienceVisit.medium,
        AudienceVisit.campaign,AudienceVisit.country,AudienceVisit.language,AudienceVisit.device,
        AudienceVisit.browser,AudienceVisit.entry_page,AudienceVisit.exit_page,AudienceVisit.page_views,
        AudienceVisit.active_seconds,AudienceVisit.cart_at,AudienceVisit.checkout_at,AudienceVisit.cart_cleared).all()
    linked = db.session.query(AudienceOrder.visit_id,Order.status,Order.total_cents,
        PaymentCheckout.session_state,PaymentCheckout.payment_state,PaymentCheckout.last_error).join(
        Order,Order.id==AudienceOrder.order_id).outerjoin(PaymentCheckout,PaymentCheckout.order_id==Order.id).filter(
        AudienceOrder.visit_id.in_(db.select(ids.c.id))).all()
    paid = {r.visit_id for r in linked if r.status=='paid'}
    uncertain = {r.visit_id for r in linked if r.status in ('pending','processing') and (
        r.last_error or r.session_state=='PROCESSING' or r.payment_state=='SUCCESS'
        or not r.session_state)}-paid
    failed = {r.visit_id for r in linked if r.status!='paid' and r.payment_state=='FAILED'}-paid-uncertain
    orders_paid = sum(r.status=='paid' for r in linked)
    revenue = sum(r.total_cents for r in linked if r.status=='paid')/100
    n = len(rows)
    carts = sum(bool(r.cart_at) for r in rows)
    checkouts = sum(bool(r.checkout_at) for r in rows)
    abandoned = sum(bool(r.cart_at) and r.last_seen < now-timedelta(hours=24)
        and r.id not in paid|failed|uncertain for r in rows)
    quick = sum(r.page_views==1 and r.active_seconds<10 and not r.cart_at for r in rows)
    active = sum(r.last_seen >= now-timedelta(minutes=5) for r in rows)
    groups = {}
    for field in ('source','medium','campaign','country','language','device','browser','entry_page','exit_page'):
        buckets = defaultdict(lambda: [0,0])
        for r in rows:
            label = getattr(r,field) or ('Sem campanha' if field=='campaign' else 'Não identificado')
            if field == 'country' and label != 'Não identificado':
                label = Locale.parse('pt_PT').territories.get(label,label)
            if field == 'language':
                label = {'pt':'Português','en':'Inglês','fr':'Francês','es':'Espanhol'}.get(label,label)
            if field == 'medium':
                label = {'direct':'Directo / desconhecido','organic':'Pesquisa orgânica','social':'Redes sociais','cpc':'Anúncios','paid_social':'Anúncios nas redes sociais','email':'Email / newsletter','referral':'Ligação noutro website','campaign':'Outra campanha'}.get(label,label)
            if field in ('entry_page','exit_page'):
                label = PAGES.get(label,label)
            buckets[label][0] += 1
            buckets[label][1] += r.id in paid
        groups[field] = [{'label':k,'visits':v[0],'buyers':v[1],
            'rate':round(100*v[1]/v[0],1),'width':round(100*v[0]/max(1,n),1)}
            for k,v in sorted(buckets.items(),key=lambda pair:pair[1][0],reverse=True)[:10]]
    pages = db.session.query(AudiencePage.page,func.count(AudiencePage.id),
        func.avg(AudiencePage.active_seconds),func.avg(AudiencePage.scroll_percent)).filter(
        AudiencePage.visit_id.in_(db.select(ids.c.id))).group_by(AudiencePage.page).order_by(func.count(AudiencePage.id).desc()).limit(10).all()
    by_day = defaultdict(lambda: [0,0])
    for r in rows:
        day = to_local_time(r.started_at).date()
        by_day[day][0] += 1
        by_day[day][1] += r.id in paid
    maximum = max((v[0] for v in by_day.values()),default=1) or 1
    days=[]
    day=first
    while day<=last:
        v=by_day[day]
        days.append({'day':day.strftime('%d/%m'),'visits':v[0],'buyers':v[1],
                     'height':round(100*v[0]/maximum,1)})
        day += timedelta(days=1)
    paid_orders = Order.query.filter(Order.created_at>=start,Order.created_at<end,Order.status=='paid')
    global_sales = paid_orders.with_entities(func.count(Order.id),func.coalesce(func.sum(Order.total_cents),0)).one()
    steps = [('Visitas observadas',n,'Entrada no website'),('Carrinho',carts,'Adicionaram bilhetes ou retomaram uma encomenda'),
             ('Checkout iniciado',checkouts,'Encomenda criada ou retomada'),('Compra confirmada',len(paid),'Pagamento confirmado no servidor')]
    tips=[]
    if n==0:
        tips.append(('Vamos começar a ouvir o público','As primeiras visitas aparecem após os visitantes permitirem estatísticas. Os dados de navegação anteriores à activação não existem.'))
    if abandoned:
        tips.append(('Há carrinhos para compreender',f'{abandoned} visitas com carrinho ficaram sem compra há pelo menos 24 horas. É uma estimativa; estas encomendas não foram canceladas.'))
    if failed:
        tips.append(('Atenção ao pagamento',f'{len(failed)} visitas tiveram uma tentativa falhada sem compra posterior na mesma visita. O motivo bancário pode não estar disponível.'))
    if uncertain:
        tips.append(('Há pagamentos a aguardar retorno',f'{len(uncertain)} visitas têm pagamentos por verificar. Não são classificadas como abandono.'))
    if n and not tips:
        tips.append(('Segue o percurso, sem precipitar conclusões','Compara vários dias antes de alterar uma campanha. Poucas visitas podem produzir percentagens muito instáveis.'))
    sources=groups['source']
    if n>=20 and sources and sources[0]['visits']>=5:
        tips.append(('De onde chega mais gente',f"{sources[0]['label']} trouxe {sources[0]['visits']} visitas observadas. Vê também quantas chegaram à compra antes de investir mais nessa origem."))
    return dict(first=first,last=last,visits=n,visitors=len({r.visitor_key for r in rows}),
        returning=sum(r.is_returning for r in rows),page_views=sum(r.page_views for r in rows),
        seconds=round(sum(r.active_seconds for r in rows)/n) if n else 0,
        rate=round(100*len(paid)/n,1) if n else 0,quick=quick,active=active,carts=carts,
        checkouts=checkouts,buyers=len(paid),orders_paid=orders_paid,revenue=revenue,
        abandoned=abandoned,failed=len(failed),uncertain=len(uncertain),groups=groups,
        pages=[dict(label=PAGES.get(p,p),views=c,seconds=round(s or 0),scroll=round(sc or 0)) for p,c,s,sc in pages],
        days=days,steps=[dict(label=a,value=b,note=c,width=round(100*b/max(1,n),1)) for a,b,c in steps],tips=tips,
        global_paid=global_sales[0],global_revenue=global_sales[1]/100)


def init_audience_admin(bp, admin_required):
    @bp.get('/audience')
    @admin_required
    def audience_admin():
        return render_template('admin/audience.html',a=report())
