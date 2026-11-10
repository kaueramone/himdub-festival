"""Customer processing fees, applied to every product category.

Teya: 2.09% of the basket subtotal, rounded once to the nearest cent
(half up), with no fixed charge. Stripe retains its existing per-unit
calculation. Receipts use the stored order total, never today's provider.
"""

STRIPE_FEE_PERCENT = 0.015  # 1,5%
STRIPE_FEE_FIXED_CENTS = 25  # 0,25€


def processing_fee_cents(price_cents, quantity=1):
    """Taxa de processamento (cêntimos) para `quantity` unidades vendidas
    a `price_cents` cada -- 1,5% do preço da unidade + 0,25€, por unidade
    (não uma vez só pelo total)."""
    per_unit = round(price_cents * STRIPE_FEE_PERCENT) + STRIPE_FEE_FIXED_CENTS
    return per_unit * quantity


def cart_processing_fee_cents(lines, provider="stripe"):
    """Calculate the selected provider's fee using current catalog prices."""
    if provider == "teya":
        subtotal = sum(line["product"].effective_price_cents * line["quantity"] for line in lines)
        return (subtotal * 209 + 5000) // 10000
    return sum(
        processing_fee_cents(line["product"].effective_price_cents, line["quantity"])
        for line in lines
    )


def order_processing_fee_cents(order_items, provider="stripe"):
    """Calculate the new order fee using its captured item prices."""
    if provider == "teya":
        subtotal = sum(item.price_cents * item.quantity for item in order_items)
        return (subtotal * 209 + 5000) // 10000
    return sum(processing_fee_cents(item.price_cents, item.quantity) for item in order_items)


def stored_processing_fee_cents(order):
    """Use the amount originally charged, even after prices/provider change."""
    return max(0, order.total_cents - sum(item.price_cents * item.quantity for item in order.items))
