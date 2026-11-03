# Operação do HIM Dub Festival

## Contas e pagamentos

O endereço para serviços novos é himdubfestival.ti@gmail.com. Os serviços e
o código devem continuar sob controlo do festival. O Stripe mantém-se como
processador de pagamentos. A Teya está em negociação e não faz parte desta
configuração nem altera o checkout atual.

## Duas configurações distintas

- `.env` nesta pasta: variáveis da aplicação Flask, seguindo `.env.example`.
- `.env.local` na pasta superior: inventário privado de acessos entregue pelo
  responsável. É texto livre, não é carregado pela aplicação e não deve ser
  copiado para o Git nem tratado como configuração Flask.

Os ficheiros de acessos não estão encriptados pelo `.gitignore`: este apenas
evita a sua inclusão acidental no Git. Não usar `git add -f` nesses ficheiros.
As credenciais devem ser mantidas num gestor de palavras-passe.

## Preparar uma instalação

1. Criar um ambiente virtual e instalar `requirements.txt`.
2. Copiar `.env.example` para `.env` e preencher apenas os acessos do ambiente
   pretendido. Para desenvolver, usar SQLite e Stripe em modo de teste.
3. Em produção, configurar uma `SECRET_KEY` privada e aleatória no Render.
   O arranque passa a recusar a ausência dessa variável em Render ou com
   `DOMAIN` HTTPS. Não publicar esta alteração antes de confirmar a variável.
4. Não executar `seed.py` nem testes contra a base de produção. Executar apenas
   as migrações de lançamento revistas, após verificar o backup.
5. Validar localmente antes de qualquer deploy. A configuração real de
   arranque do Render deve ser conferida; o `Procfile` não prova qual está ativa.

Sem `SECRET_KEY`, apenas em desenvolvimento, é gerada uma chave temporária.
Reiniciar o processo invalida as sessões locais. Em produção, todos os processos
devem partilhar a mesma chave privada.

## MCP

Os MCP são configurados no Codex desta máquina, fora do repositório.
Os endpoints oficiais são:

- Render: https://mcp.render.com/mcp
- Resend: https://mcp.resend.com/mcp
- Stripe: https://mcp.stripe.com

Um endpoint configurado não significa autenticação concluída. O OAuth deve
ser concluído na conta/equipa do festival e validado com uma consulta.
Na primeira fase, consultar configuração e estado; alterações de pagamentos,
envios de email e deploys exigem uma tarefa concreta que os justifique.

Em 03/11/2026, Render, Stripe e Resend foram autenticados e validados por consultas.
O Stripe está limitado a leitura na conta de produção DECIBELCOSTUME LDA.
O OAuth Resend concede acesso total, mas a configuração local limita as
ferramentas desta ligação a consultas. Essa lista só entra em vigor nas
ligações recarregadas. O Render usa o cliente OAuth pré-registado `codex`,
conforme as instruções oficiais; o consentimento tem as permissões da conta,
mas a configuração local também limita as ferramentas a consultas.

O serviço Render `himdub-festival` passou para `kaueramone/himdub-festival`,
branch `main`, com deploy automático após os testes de CI passarem.
O arranque é `gunicorn --config gunicorn.conf.py app:app`, na raiz do repositório.
O Python é fixado na série 3.13 por `.python-version`. A verificação `/healthz`
confirma a ligação e o esquema da base de dados. A base e os domínios existentes
foram preservados. As migrações e a preparação inicial das contas correm no
pre-deploy com o consumidor de emails desligado nesse processo.

## Bunny

A conta do festival tem armazenamento e CDN próprios. Os três vídeos e o áudio
foram carregados, verificados e servidos por
`CDN_BASE_URL=https://himdubfestival-media.b-cdn.net`. Os objetos mantêm os
caminhos `static/video/` e `static/audio/`. A chave de gestão Bunny nunca é
enviada ao browser nem é necessária no Render para reproduzir estes conteúdos.

## Organização e histórico

A aplicação ativa está nesta pasta, com entrada `app:app`. A segunda cópia
aninhada foi preservada na pasta privada superior
`audit/bootstrap-backups/legacy-nested-project`, acompanhada de comparação
em `audit/bootstrap-duplicate-comparison.json`. O template exclusivo da cópia
antiga continua nesse arquivo. `_to_delete/` permanece local e ignorado.

A origem publicada é https://github.com/kaueramone/himdub-festival.git.
Foi feita uma importação limpa: o histórico legado não foi publicado.
Não publicar referências antigas com `git push --all` ou `--mirror`.
Os scans do código e do histórico publicado não encontraram credenciais;
um scan não constitui garantia de ausência de segredos.

O histórico contém uma chave de sessão predefinida e uma palavra-passe
predefinida de bloqueio do site. Confirmar se foram utilizadas em produção;
se sim, substituí-las no serviço antes de publicar o histórico. A substituição
da chave de sessão termina as sessões existentes. A correção local não
substitui automaticamente as variáveis já configuradas no Render.

## Registo da migração de 03/11/2026

Destino publicado e ligado ao Render: https://github.com/kaueramone/himdub-festival.git.
O backup foi criado no Render antes de executar a migração aditiva.
A conferência posterior preservou 418 encomendas e 444 bilhetes.
Foi substituída a chave de sessão e preparado um acesso individual com MFA;
as sessões antigas e as palavras-passe partilhadas deixam de dar acesso administrativo.

1. Trabalhar na aplicação ativa desta pasta e concluir as correções de segurança
   e as otimizações identificadas na auditoria. A limpeza inicial não encerra
   a auditoria: continuam pendentes, entre outros, validação de pagamentos,
   recuperação da emissão de bilhetes, concorrência/stock, proteção dos dados
   de confirmação, check-in, CSRF e desempenho da área administrativa.
2. Validar os fluxos em ambiente isolado e preparar a configuração de produção,
   as migrações necessárias, uma cópia de segurança da base de dados e a
   recuperação em caso de falha. Não usar compradores reais nos testes.
3. Preparar uma primeira versão sem credenciais nem histórico sensível e
   publicá-la no destino indicado. Rever previamente qualquer conteúdo que
   já exista nesse destino, sem o substituir por força.
4. Só depois alterar a ligação GitHub do serviço Render existente, confirmando
   que a integração tem acesso ao novo repositório. Coordenar o deploy
   automático, confirmar branch e commit e validar o deploy antes de o
   considerar concluído. Manter a base de dados e as configurações do serviço.

A sequência acima documenta o procedimento seguido. A implementação das novas ferramentas administrativas segue o escopo
contratado e não deve ser confundida com esta limpeza da versão recebida.


## Base de segurança preparada em 03/11/2026

As verificações acima orientaram as correções desta primeira versão: assinatura
Stripe obrigatória, correspondência de pagamento/valor/moeda/ambiente, emissão
transacional com recuperação, reservas de stock, confirmação por URL assinada,
check-in condicionado a entrada válida, pulseiras únicas, fila persistente de
emails, contas individuais, MFA administrativo, limitação de tentativas e CSRF.
A exportação Excel trata texto do cliente como texto; a lista administrativa é
paginada e as estatísticas são agregadas na base. As dependências afetadas foram
atualizadas e o conjunto de versões auditado está em `constraints.txt`.

As funcionalidades novas do projeto (CMS, gestão editorial dos lotes e restantes
ferramentas contratadas) continuam como trabalho próprio. A newsletter deixa de
confirmar uma subscrição que ainda não existe; a integração de marketing é uma
entrega posterior. O glamping continua a depender do parceiro, e a operação de
merchandise com expedição pertence à fase acordada para a loja.

A migração acrescenta tabelas e uma coluna de reserva. Em produção é executada
explicitamente com `EMAIL_WORKER_ENABLED=false flask --app app db-upgrade` antes
do arranque. Criar as contas da equipa e configurar os autenticadores no lançamento
da nova administração: as passwords partilhadas são recusadas em produção.
A proteção de unicidade não remove nem corrige pulseiras antigas automaticamente.
Em 03/11/2026, uma consulta de leitura à produção não encontrou duplicados.

O comando `staff-bootstrap` cria apenas a primeira conta a partir de variáveis
temporárias privadas. Com contas existentes, não altera acessos por defeito.
Para recuperação explícita de MFA, `STAFF_BOOTSTRAP_ROTATE_MFA=true`, juntamente
com `STAFF_BOOTSTRAP_EMAIL` e um novo `STAFF_BOOTSTRAP_TOTP_SECRET`, substitui o
segredo apenas do administrador ativo indicado, revoga sessões e regista a ação.
Retirar estas variáveis temporárias após confirmar a operação. Nunca incluir
segredos, QR de configuração ou passwords nos logs, documentação ou Git.

Os eventos de reembolso integral invalidam a entrada associada. Reembolsos parciais
exigem uma política funcional própria; o código não presume que anulam todos os
bilhetes. O estado de email `sent` significa aceite pelo fornecedor. Confirmação
`delivered`/`bounced` por webhook permanece uma evolução da monitorização.

## Edição dos conteúdos atuais

Em Conteúdos, The Experience, Participate e as páginas FAQ, Sustentabilidade,
Parceiros e Contactos incluem os itens originais, com Editar e Duplicar.
O catálogo inicial em `content_catalog.py` preserva os textos, imagens e destinos
existentes. As alterações e cópias ficam na tabela aditiva `native_contents`,
criada pelo mesmo `db-upgrade`; consultar a listagem não modifica a base.
Não existe uma importação que possa voltar a escrever por cima de edições.

Os itens são apresentados no local e formato originais. Uma cópia começa como
rascunho; desmarcar Publicar retira o item da página pública. As traduções
existentes são preenchidas no editor e devem ser revistas quando o português
muda. Os blocos adicionais continuam disponíveis e também podem ser duplicados.
Os conteúdos da loja e os álbuns mantêm as suas ferramentas próprias.
