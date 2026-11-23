# HIM Dub Festival

Website Flask/Jinja com loja, bilhetes e ferramentas internas de administração.
O Stripe mantém-se como processador de pagamentos. A Teya está em avaliação.

## Desenvolvimento local

Usar Python 3.12 ou 3.13 e um ambiente virtual:

```sh
python -m venv .venv
# Ativar o ambiente virtual conforme o sistema operativo.
pip install -r requirements.txt
```

Copiar `.env.example` para `.env`. Apenas para desenvolvimento, sem Stripe
configurado, o checkout funciona em modo demo, sem cobrança nem envio de email.
A aplicação cria uma base SQLite local; `seed.py` serve para dados de demonstração.
Nunca executar o seed na base de produção.

```sh
python app.py
python -m unittest discover -s tests -v
```

Não colocar `.env`, `.env.local`, credenciais ou bases de dados no Git.
O inventário privado de acessos na pasta superior não é carregado pela aplicação.

## Contas da equipa

```sh
flask --app app user-add himdubfestival.ti@gmail.com --role admin
flask --app app user-revoke email-da-conta
```

A criação pede uma palavra-passe de pelo menos 14 caracteres e apresenta o URI
para a aplicação de autenticação. Guardá-lo em privado. O MFA é obrigatório para
administradores em produção. Para operadores de entrada, usar `--role checkin`.
Para apoio ao cliente, usar `--role customer_service` (MFA obrigatório em produção),
ou escolher **Customer Service** no convite em Equipa. Este cargo acede apenas a
Início, Vendas e Encomendas, incluindo exportações, edição e reenvio dos bilhetes.
Pode cancelar bilhetes individuais ainda sem check-in, com motivo e confirmação.
O cancelamento invalida a entrada e mantém o pagamento e o histórico; não efectua
reembolsos nem liberta automaticamente lugares de lote. Pagamentos de troca de
titular em curso exigem intervenção de um administrador antes do cancelamento.
As contas e sessões podem ser revogadas individualmente. As antigas palavras-passe
partilhadas só são aceites em desenvolvimento.

## Preparação de produção

1. Confirmar um backup recente e a recuperação da base PostgreSQL.
2. Configurar `SECRET_KEY` privada com pelo menos 32 caracteres, `DATABASE_URL`,
   `DOMAIN` HTTPS, chaves Stripe, segredo do webhook e Resend.
3. Configurar no Render o comando anterior ao deploy:

```sh
EMAIL_WORKER_ENABLED=false flask --app app db-upgrade
```

4. Arranque: `gunicorn --config gunicorn.conf.py app:app`; health check: `/healthz`.
5. Criar as contas individuais e configurar os autenticadores antes de entregar
   o acesso administrativo. A chave de sessão deve ser comum aos processos.
6. Confirmar os eventos Stripe: `checkout.session.completed`,
   `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`,
   `checkout.session.expired` e `charge.refunded`.
7. Verificar pagamentos e emails em modo de teste num ambiente separado. Não
   simular compras nem alterações a bilhetes de clientes na base de produção.

As migrações de produção são explícitas; não são executadas por cada processo web.
A proteção de pulseiras únicas exige que não existam associações duplicadas.
A fila persistente de emails é consumida por uma thread por processo web, com
repetições e chaves de idempotência. Pode ser acompanhada em `/admin/email-queue`.
O estado `sent` significa aceite pelo Resend, não entrega confirmada ao destinatário.

## Primeira publicação

Destino: https://github.com/kaueramone/himdub-festival.
A primeira versão usa um commit de raiz, sem publicar o histórico recebido.
A alteração de origem no Render deve acontecer depois da validação do novo
repositório, mantendo a base de dados e as configurações do serviço existente.

Ver [operação e sequência de migração](docs/OPERATIONS.md).


### Público e comportamento

Área exclusiva de administradores em `/admin/audience`, integrada na base de dados
existente, sem serviço externo de analytics. Filtros de 7/30/90 dias ou datas
personalizadas; dias em Portugal continental. A recolha começa após consentimento
expresso, exclui equipa autenticada, bots identificados, DNT e GPC. Não reconstrói
visitas anteriores nem representa quem recusa estatísticas.

- Visita: 30 minutos de inactividade; navegador estimado por identificador aleatório
  assinado, guardado sob hash (dados pseudónimos). Sem IP persistido, User-Agent
  integral, URLs/query strings integrais, campos de formulários ou gravações.
- Origem: primeira origem conhecida da visita; apenas domínio de referência e
  etiquetas públicas de campanha filtradas. Nunca colocar nomes/emails em UTM.
- Tempo: estimativa com página visível e actividade recente; heartbeat de 30 s,
  limites de 200 páginas/visita, 30 min/página e 1000 eventos/hora por consentimento.
- Funil: carrinho confirmado no servidor → encomenda criada/retomada → estado
  `paid` real. Uma visita pode ter várias encomendas; conversão conta a visita
  uma única vez. Compras demo e bilhetes oferecidos não são conversões.
- Abandono: carrinho sem compra observada há 24h, sem falha conhecida nem pagamento
  por verificar; não cancela encomendas nem liberta stock. Falha seguida de compra
  na mesma visita conta como conversão. Vendas globais aparecem separadamente.
- Retenção: 90 dias de navegação; limpeza horária no trabalhador existente e na
  recolha. Comando operacional idempotente: `flask --app app audience-prune`.
  Retirar consentimento elimina visitas/páginas/atribuição e mantém encomendas.
  Preferência `himdub_statistics`: 180 dias; identificador `himdub_audience`:
  90 dias; visita `himdub_visit`: 30 min. Cookies HttpOnly, SameSite=Lax e Secure
  em produção. Preferências acessíveis no rodapé; textos PT/EN/FR/ES revistos.

Novas tabelas aditivas criadas pelo `db-upgrade`: `audience_consents`,
`audience_visits`, `audience_pages` e `audience_orders`. As falhas de atribuição
estão isoladas por savepoint e não devem impedir compras. A navegação pública
com grant/consentimento usa `private, no-store`; assets continuam em cache.
