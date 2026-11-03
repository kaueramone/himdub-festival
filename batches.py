"""Sistema de "lotes" (batches) de bilhetes com limite de unidades e/ou
data de fecho.

Exemplo real: os primeiros 200 "Bilhete Geral" vendidos ficam no preço
Early Bird (140€). A partir da venda #201, o preço passa automaticamente
para o 2º lote (180€) -- sem ser preciso mexer em nada à mão, nem trocar
produtos no admin. Basta as vendas confirmadas atingirem o limite.

Do 2º lote em diante (Duage, Ananse Ntontan, Djwennimmen) NÃO há um número
de unidades definido -- é a organização que decide, mais para a frente, em
que data cada um desses lotes fecha e o seguinte abre. Até essa decisão
ser tomada, um lote sem "until" fica à venda indefinidamente (não fecha
sozinho). Quando a data for decidida, basta preencher o "until" desse
lote (ver exemplo em baixo) -- não é preciso mexer em mais nada, o preço
troca sozinho à meia-noite dessa data.

Como funciona:

- TICKET_BATCHES define, por slug de produto, a lista de lotes por ordem
  (1º, 2º, ...). Cada lote tem:
    - "limit": nº de unidades deste lote (None = sem limite de unidades).
    - "until": última data (objeto `date`) em que este lote ainda está à
      venda -- no dia seguinte passa automaticamente para o lote a seguir
      (None = sem data marcada, fica à venda até termos as unidades
      esgotadas ou decidirmos uma data).
    - "price_cents": preço deste lote.
  Um lote fecha quando QUALQUER UM dos dois critérios acontece primeiro
  (esgotar as unidades OU passar a data). Ajusta aqui quando quiseres
  abrir um novo lote, mudar um limite ou marcar a data de fecho de um
  lote -- não precisas de tocar em mais nada.

  Exemplo de como marcar a data de fecho do 2º lote (Duage), quando a
  organização decidir: trocar `"until": None` por `"until": date(2027, 3, 1)`
  nesse lote (isto fecha o Duage no fim de 28 de fevereiro de 2027 e abre
  o Ananse Ntontan a partir de 1 de março de 2027).

  - "real_limit": como "limit", mas em vez de contar TODAS as vendas
    pagas (incluindo modo demo) desde sempre, só conta bilhetes de
    encomendas pagas A SÉRIO no Stripe ("paid", nunca "demo-paid") E só a
    partir do momento em que a contagem de vendas reais for ativada no
    admin (botão "Ativar contagem de vendas reais", em /admin/orders --
    ver real_sold_count/real_sales_launch_at, mais abaixo). Enquanto não
    for ativada, real_sold_count() devolve sempre 0 -- ou seja, um lote
    com "real_limit" NUNCA fecha por vendas antes de o João carregar
    nesse botão, o que só deve acontecer no dia em que o site passar a
    usar o domínio final himdubfestival.com (tudo o que se vender antes
    disso, no domínio provisório, são testes e não deve contar).

    Quando um lote tem "real_limit" definido, é ELE (e não o "limit") que
    decide quando esse lote fecha -- o "limit" nesse caso fica só
    informativo, para continuar a mostrar o mesmo número ao público em
    "Limited to X" (ver templates/tickets.html), mesmo que o número real
    de unidades desse lote seja outro (pedido explícito do João: o
    "limit" público do Early Bird continua a dizer 200, mas o lote fecha
    mesmo é aos 150 bilhetes reais -- na prática vendem-se sempre mais
    umas 50 unidades do que o que aparece no site).

- BUNDLES define, por slug de "pacote" (ex: Full Pass + Caravan), quais os
  bilhetes que esse pacote representa. Ao contrário de um bilhete normal,
  um pacote gera um ÚNICO Ticket/QR combinado (dá acesso a tudo o que
  inclui) -- ver ticketing.py. O preço do pacote é sempre a soma dos
  preços ATUAIS dos componentes, por isso acompanha os lotes
  automaticamente (quando o Full Pass passa para o 2º lote, o pacote
  "Full Pass + Caravan" sobe de 220€ para 260€ sozinho). E cada bilhete de
  pacote vendido conta na mesma para o limite do(s) componente(s) com
  lotes que inclui -- ex: vender 1x "Full Pass + Caravan" consome 1 dos
  200 lugares Early Bird do Full Pass, tal como uma venda avulsa -- ver
  sold_count().

- Um bilhete só conta para o limite de um lote quando a encomenda que o
  gerou fica mesmo paga (não à cabeça, ainda no carrinho).
"""

from datetime import date, datetime

from extensions import db

TICKET_BATCHES = {
    "bilhete-geral": [
        # "limit": 200 fica só para o número mostrado ao público em
        # "Limited to 200" -- quem decide mesmo quando este lote fecha e o
        # 2nd batch abre é o "real_limit": 150 (ver comentário acima).
        {"limit": 200, "real_limit": 150, "until": None, "price_cents": 14000},  # 1º lote -- Early Bird (Akoma)
        {"limit": None, "until": None, "price_cents": 18000},  # 2º lote (Duage)
        {"limit": None, "until": None, "price_cents": 22000},  # 3º lote (Ananse Ntontan)
        {"limit": None, "until": None, "price_cents": 28000},  # 4º lote -- último (Djwennimmen)
    ],
    # Bilhete Fim de Semana e Bilhete Família também passam a ter lotes,
    # tal como o Bilhete Geral -- pedido explícito do João, para o preço
    # (e a arte do bilhete, ver ticket_image.py) poderem subir sozinhos em
    # lotes futuros sem ser preciso mexer em mais nada, exatamente como já
    # acontece com o Bilhete Geral acima. Por agora só têm o 1º lote
    # (Early Bird) definido, sem limite de unidades nem data de fecho --
    # quando a organização decidir abrir um 2º lote, basta acrescentar
    # aqui uma entrada nova (preço + arte em ticket_image.py), como se fez
    # para o Bilhete Geral.
    "bilhete-fim-de-semana": [
        {"limit": None, "until": None, "price_cents": 17000},  # 1º lote -- Early Bird (Bi Nka Bi)
    ],
    "bilhete-familia": [
        {"limit": None, "until": None, "price_cents": 34000},  # 1º lote -- Early Bird (Sankofa)
    ],
}

# Chave usada na tabela "settings" (ver models.py) para guardar a partir de
# que instante (UTC, ISO 8601) contam as "vendas reais" -- ver
# real_sold_count/real_sales_launch_at/activate_real_sales_launch, abaixo.
REAL_SALES_LAUNCH_SETTING_KEY = "real_sales_launch_at"

# A partir de quantos bilhetes reais em falta é que avisamos a organização
# (banner no admin + email, ver check_and_flag_low_stock_alert e
# blueprints/shop.py _check_low_stock_alerts).
LOW_STOCK_ALERT_THRESHOLD = 10

# slug do pacote -> slugs dos bilhetes que ele contém (um Ticket por
# componente, por unidade comprada do pacote).
BUNDLES = {
    "full-pass-caravan": ["bilhete-geral", "passe-caravan"],
}


def _slugs_counting_towards(product_slug):
    """product_slug em si, mais qualquer pacote que o inclua -- um bilhete
    de pacote vendido conta para o limite do componente na mesma, mesmo
    sendo um único Ticket combinado (ver módulo acima)."""
    slugs = [product_slug]
    for bundle_slug, components in BUNDLES.items():
        if product_slug in components:
            slugs.append(bundle_slug)
    return slugs


def sold_count(product_slug):
    """Quantos bilhetes deste tipo já foram emitidos em encomendas pagas
    -- conta bilhetes do próprio produto E de qualquer pacote que o
    inclua (ex: vendas de "Full Pass + Caravan" contam para o limite do
    Full Pass)."""
    from models import Ticket, Order
    return (
        db.session.query(Ticket)
        .join(Order, Ticket.order_id == Order.id)
        .filter(Ticket.product_slug.in_(_slugs_counting_towards(product_slug)))
        .filter(Order.status.in_(["paid", "demo-paid"]))
        .count()
    )


def real_sales_launch_at():
    """Devolve o datetime UTC (naive) a partir do qual real_sold_count()
    passa a contar vendas, ou None se a contagem ainda não foi ativada no
    admin. Enquanto for None, NENHUMA venda conta para um "real_limit" --
    é assim que garantimos que os testes feitos no domínio provisório
    (himdub-festival.onrender.com) nunca contam para o 2nd batch."""
    from models import Setting
    row = Setting.query.get(REAL_SALES_LAUNCH_SETTING_KEY)
    if not row or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


def activate_real_sales_launch():
    """Liga o interruptor, AGORA -- chamado uma única vez pelo botão
    "Ativar contagem de vendas reais" em /admin/orders, exatamente no
    momento em que o site passa a usar o domínio final himdubfestival.com.
    Tudo o que foi vendido até este instante (mesmo no domínio provisório)
    fica de fora da contagem do "real_limit" -- são só testes. Devolve o
    datetime UTC gravado."""
    from models import Setting
    now = datetime.utcnow()
    row = Setting.query.get(REAL_SALES_LAUNCH_SETTING_KEY)
    if row is None:
        db.session.add(Setting(key=REAL_SALES_LAUNCH_SETTING_KEY, value=now.isoformat()))
    else:
        row.value = now.isoformat()
    db.session.commit()
    return now


def real_sold_count(product_slug):
    """Como sold_count(), mas só conta bilhetes de encomendas pagas A
    SÉRIO no Stripe (status "paid" -- nunca "demo-paid") E só a partir do
    momento em que a contagem de vendas reais foi ativada no admin (ver
    real_sales_launch_at). Antes disso devolve sempre 0."""
    launch_at = real_sales_launch_at()
    if launch_at is None:
        return 0

    from models import Ticket, Order
    return (
        db.session.query(Ticket)
        .join(Order, Ticket.order_id == Order.id)
        .filter(Ticket.product_slug.in_(_slugs_counting_towards(product_slug)))
        .filter(Order.status == "paid")
        .filter(Order.created_at >= launch_at)
        .count()
    )


def _reserved_count(product_slug, real_only=False):
    from models import Order, OrderItem, Product
    from sqlalchemy import func, or_, and_
    query = (db.session.query(func.coalesce(func.sum(OrderItem.quantity), 0))
             .join(Order, OrderItem.order_id == Order.id)
             .join(Product, OrderItem.product_id == Product.id)
             .filter(Product.slug.in_(_slugs_counting_towards(product_slug)),
                     or_(and_(Order.status == "pending", Order.reservation_expires_at > datetime.utcnow()),
                         Order.status == "processing")))
    if real_only:
        launch_at = real_sales_launch_at()
        if launch_at is None:
            return 0
        query = query.filter(Order.created_at >= launch_at)
    return query.scalar()


def current_tier(product_slug):
    """Devolve (indice_do_lote (1-based), preco_cents, restantes_ou_None)
    do lote atualmente à venda para este produto, ou None se o produto não
    tem lotes (preço fixo) ou se todos os lotes já fecharam (esgotados por
    unidades ou por já termos passado a data marcada em "until").

    Um lote fecha quando QUALQUER UM dos dois critérios se verifica
    primeiro -- esgotar o limite de unidades (ou "real_limit", ver
    comentário no topo do ficheiro) OU passar a data marcada em "until"."""
    tiers = TICKET_BATCHES.get(product_slug)
    if not tiers:
        return None

    today = date.today()
    cumulative = 0
    real_cumulative = 0
    real_sold = None  # só calculado (1 query) se algum lote tiver "real_limit"
    sold = None  # idem, para "limit" normal
    for idx, tier in enumerate(tiers, start=1):
        limit = tier.get("limit")
        real_limit = tier.get("real_limit")
        until = tier.get("until")

        if limit is not None:
            cumulative += limit
        if real_limit is not None:
            real_cumulative += real_limit

        if real_limit is not None:
            if real_sold is None:
                real_sold = real_sold_count(product_slug) + _reserved_count(product_slug, real_only=True)
            has_stock = real_sold < real_cumulative
        elif limit is not None:
            if sold is None:
                sold = sold_count(product_slug) + _reserved_count(product_slug)
            has_stock = sold < cumulative
        else:
            has_stock = True

        still_open = until is None or today <= until

        if has_stock and still_open:
            if limit is not None and real_limit is None:
                if sold is None:
                    sold = sold_count(product_slug) + _reserved_count(product_slug)
                remaining = cumulative - sold
            else:
                remaining = None
            return (idx, tier["price_cents"], remaining)

    return None  # todos os lotes fecharam


def real_remaining_in_current_tier(product_slug):
    """Quantos bilhetes reais (Stripe pago, contados a partir do
    lançamento -- ver real_sold_count) ainda faltam vender para o lote
    ATUAL fechar e o seguinte abrir. Só faz sentido quando o lote atual
    tem "real_limit" definido (ver TICKET_BATCHES) -- nos outros casos
    (sem limite nenhum, ou só o "limit" normal) devolve None, porque não
    há aqui nenhum número "a acabar" para monitorizar.

    Usado para o aviso de stock baixo no admin e por email -- ver
    check_and_flag_low_stock_alert, abaixo, e blueprints/admin.py."""
    tiers = TICKET_BATCHES.get(product_slug)
    if not tiers:
        return None
    cur = current_tier(product_slug)
    if cur is None:
        return None
    cur_idx = cur[0]
    tier = tiers[cur_idx - 1]
    if tier.get("real_limit") is None:
        return None
    real_cumulative = sum(
        t.get("real_limit") for t in tiers[:cur_idx] if t.get("real_limit") is not None
    )
    return real_cumulative - real_sold_count(product_slug)


def check_and_flag_low_stock_alert(product_slug):
    """Chamado sempre que uma encomenda com bilhetes deste produto fica
    paga (ver blueprints/shop.py _check_low_stock_alerts) -- se o lote
    atual tiver LOW_STOCK_ALERT_THRESHOLD bilhetes reais ou menos por
    vender, E ainda não tivermos avisado para ESTE lote em concreto,
    marca o aviso como enviado (para nunca duplicar o email, mesmo que
    várias vendas cheguem quase ao mesmo tempo) e devolve
    (indice_do_lote, restantes) para quem chamou despachar o email.
    Devolve None quando não há nada de novo para avisar."""
    from models import Setting
    remaining = real_remaining_in_current_tier(product_slug)
    if remaining is None or remaining < 0 or remaining > LOW_STOCK_ALERT_THRESHOLD:
        return None

    tier_idx = current_tier(product_slug)[0]
    key = f"low_stock_alert_sent:{product_slug}:{tier_idx}"
    if Setting.query.get(key):
        return None
    db.session.add(Setting(key=key, value="1"))
    db.session.commit()
    return (tier_idx, remaining)


def batch_tier_index(product_slug):
    """Índice do lote atual (1, 2, ...) para um produto com lotes, ou None
    se não tiver lotes (preço fixo) ou se já estiver esgotado."""
    tier = current_tier(product_slug)
    return tier[0] if tier else None


def remaining_in_current_tier(product_slug):
    tier = current_tier(product_slug)
    return tier[2] if tier else None


def all_tiers_with_status(product_slug):
    """Devolve a lista de TODOS os lotes definidos para este produto, cada
    um com o seu estado atual:
      - "active"   -- é o lote à venda neste momento
      - "sold_out" -- já passou (esgotado por unidades ou por data) --
                      mostrado riscado na página de bilhetes
      - "upcoming" -- ainda não chegou a vez (só entra quando o(s)
                      anterior(es) fechar(em))
    Usado para mostrar o lote atual em destaque e a lista dos restantes
    lotes por baixo (ver templates/tickets.html). Devolve [] para produtos
    sem lotes (preço fixo)."""
    tiers = TICKET_BATCHES.get(product_slug)
    if not tiers:
        return []

    cur = current_tier(product_slug)
    cur_idx = cur[0] if cur else None

    result = []
    for idx, tier in enumerate(tiers, start=1):
        if cur_idx is None or idx < cur_idx:
            status = "sold_out"
        elif idx == cur_idx:
            status = "active"
        else:
            status = "upcoming"
        result.append({
            "idx": idx,
            "price_cents": tier["price_cents"],
            "limit": tier.get("limit"),
            "remaining": cur[2] if (status == "active" and cur) else None,
            "status": status,
        })
    return result


def is_sold_out(product_slug):
    """True só quando o produto tem lotes definidos e TODOS esgotaram
    (incluindo o último, que normalmente não tem limite)."""
    if product_slug not in TICKET_BATCHES:
        return False
    return current_tier(product_slug) is None


def effective_price_cents(product_slug):
    """Preço atual (em cêntimos) para este slug, já com lotes/pacotes em
    conta. Devolve None para produtos sem lote nem pacote (nesse caso
    usa-se o price_cents guardado no próprio Product)."""
    if product_slug in BUNDLES:
        total = 0
        for comp_slug in BUNDLES[product_slug]:
            comp_price = effective_price_cents(comp_slug)
            if comp_price is None:
                from models import Product
                comp_product = Product.query.filter_by(slug=comp_slug).first()
                comp_price = comp_product.price_cents if comp_product else 0
            total += comp_price
        return total

    tier = current_tier(product_slug)
    if tier is None:
        return None
    return tier[1]


def resolve_batch_tier_for_order_item(product_slug):
    """Lote a gravar no OrderItem no momento do checkout, para depois se
    gerarem os bilhetes com o template/preço certos (ver ticketing.py e
    ticket_image.py). Para pacotes, é o lote do componente com lotes (só
    um, nos casos atuais); para produtos com lotes diretos, o seu próprio;
    caso contrário None."""
    if product_slug in BUNDLES:
        for comp_slug in BUNDLES[product_slug]:
            tier_idx = batch_tier_index(comp_slug)
            if tier_idx is not None:
                return tier_idx
        return None
    return batch_tier_index(product_slug)


