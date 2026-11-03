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
