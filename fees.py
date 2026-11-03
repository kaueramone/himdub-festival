"""Taxa de processamento (para cobrir a comissão da Stripe).

A Stripe cobra ao festival, por cada pagamento com cartão/MB WAY europeu,
qualquer coisa como 1,5% do valor + 0,25€ fixos. Para o festival não
absorver esse custo sozinho, esta taxa é passada para quem compra --
pedido explícito do João -- calculada por unidade/bilhete comprado (não
uma única vez por encomenda) e aplicada a QUALQUER produto vendido no
site (bilhetes, glamping, merchandise), não só bilhetes.

Tudo em cêntimos (int), tal como o resto do dinheiro no projeto
(`Product.price_cents`, `OrderItem.price_cents`, `Order.total_cents`) --
evita problemas de arredondamento com floats. Quem precisar do valor em
euros (para mostrar num template) converte no fim, com `/100`.
"""

STRIPE_FEE_PERCENT = 0.015  # 1,5%
STRIPE_FEE_FIXED_CENTS = 25  # 0,25€


def processing_fee_cents(price_cents, quantity=1):
    """Taxa de processamento (cêntimos) para `quantity` unidades vendidas
    a `price_cents` cada -- 1,5% do preço da unidade + 0,25€, por unidade
    (não uma vez só pelo total)."""
    per_unit = round(price_cents * STRIPE_FEE_PERCENT) + STRIPE_FEE_FIXED_CENTS
    return per_unit * quantity


def cart_processing_fee_cents(lines):
    """Soma a taxa de processamento de uma lista de linhas do carrinho/
    checkout (dicts com 'product' e 'quantity', como devolve
    `_cart_lines()` em blueprints/shop.py) -- usa sempre o preço efetivo
    do produto (`effective_price_cents`, já com o lote/batch em conta)."""
    return sum(
        processing_fee_cents(line["product"].effective_price_cents, line["quantity"])
        for line in lines
    )


def order_processing_fee_cents(order_items):
    """O mesmo, mas para os `OrderItem` já gravados de uma encomenda --
    usa o `price_cents` gravado em cada linha, que é o preço mesmo
    cobrado (ver `checkout()` em blueprints/shop.py), para a taxa nunca
    poder desviar-se do que foi realmente cobrado."""
    return sum(processing_fee_cents(item.price_cents, item.quantity) for item in order_items)
