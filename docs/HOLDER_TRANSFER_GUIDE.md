# Troca de titular com Teya e Stripe

O painel **Troca de titular** define ativação, taxa e prazo. A organização mantém a decisão de ativar; a publicação do código não altera esse estado nem as regras existentes. Novos pagamentos usam o fornecedor ativo em Pagamentos; pagamentos iniciados preservam o fornecedor original.

O titular pede um link no seu email, indica o novo titular e aceita as condições. O pagamento confirmado no servidor invalida o QR anterior, guarda o histórico, atribui o titular e coloca o novo bilhete na fila de envio. O regresso do navegador não confirma o pagamento. Família, Acessibilidade, bilhetes usados e encomendas não pagas não são elegíveis para troca automática.

Teya: assinatura RSA do webhook existente, consulta autenticada da sessão, correspondência de referência, montante, moeda, tipo e transação; ambiente e loja preservados. Tentativas repetidas usam a mesma chave de idempotência. A reconciliação em segundo plano recupera falhas temporárias, sem alterar o bilhete enquanto não há confirmação. A migração adiciona a coluna payment_provider com stripe para pedidos antigos e cria transfer_checkouts sem alterar pagamentos de encomendas.

Se o pagamento chegar quando o bilhete já não é elegível, a troca fica refund_pending. Em Teya, a organização devolve a taxa no painel Teya, com a autenticação exigida por esse serviço. Não existe chamada automática de reembolso Teya; não é criada uma tarefa Stripe para esse pagamento. O painel mostra fornecedor, estado, referência de transação e aviso de devolução. O Stripe mantém a sua fila automática existente.

Validação desta entrega: testes isolados com dados fictícios, API simulada e assinatura RSA, incluindo repetição, pagamento pendente, valor incorreto, troca de fornecedor, timeout com recuperação e recusa de um bilhete já usado. Não corresponde a uma cobrança real Teya. Um ensaio financeiro integral em produção requer um bilhete de teste e o pagamento pelo responsável, sem envolver compradores reais.
