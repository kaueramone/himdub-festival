"""Explain payment evidence without treating an open session as a final failure."""
def payment_info(order):
    p = order.payment_checkout
    if order.status == 'paid':
        return ('green', 'Pago', 'Pagamento confirmado. Os bilhetes foram emitidos.')
    if order.status == 'comp':
        return ('green', 'Cortesia', 'Entrada oferecida, sem pagamento necessário.')
    if order.status == 'refunded':
        return ('red', 'Reembolsado', 'Encomenda registada como reembolsada.')
    if order.status == 'demo-paid':
        return ('yellow', 'Teste', 'Encomenda de teste, separada das vendas reais.')
    if order.status == 'cancelled':
        if p and p.status == 'expired':
            return ('red', 'Pagamento expirado', 'A sessão terminou sem pagamento confirmado.')
        if p and p.status == 'failed':
            return ('red', 'Pagamento falhado', 'O fornecedor encerrou a sessão sem pagamento confirmado. Não indicou o motivo.')
        return ('red', 'Cancelado', 'Encomenda cancelada.')
    if p and p.last_error:
        return ('yellow', 'Erro de comunicação — a verificar', 'Não foi possível consultar o fornecedor. Se já pagaste, aguarda a confirmação antes de tentar novamente.')
    if p and p.payment_state == 'FAILED':
        return ('red', 'Tentativa de pagamento falhada', 'A Teya indicou uma tentativa falhada, sem comunicar o motivo. Não confirma uma recusa bancária. A sessão pode permitir nova tentativa na mesma encomenda.')
    if p and (p.session_state == 'PROCESSING' or p.payment_state == 'SUCCESS'):
        return ('yellow', 'A aguardar confirmação do pagamento', 'O pagamento está em processamento ou por confirmar. Aguarda antes de tentar novamente.')
    if p and not p.checked_at:
        return ('yellow', 'Estado do pagamento por verificar', 'Ainda não há uma consulta recente ao fornecedor. Abre o estado da encomenda antes de repetir o pagamento.')
    if order.status == 'pending' or (p and p.session_state == 'ACTIVE' and p.payment_state == 'NONE'):
        return ('yellow', 'A aguardar pagamento', 'O fornecedor ainda não registou um pagamento. Podes retomar a mesma encomenda.')
    return ('yellow', 'Em confirmação', 'A aguardar confirmação do fornecedor; ainda não conta como venda paga.')
