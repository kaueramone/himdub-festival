# Conteúdos públicos, traduções e páginas legais

## Edição das novas páginas

Em Administração → Conteúdos, escolher Regras e normas, Privacidade,
Termos e condições ou Cookies. Cada secção tem título, texto, ordem,
e a opção de publicação. É possível editar e duplicar secções.
Os conteúdos legais fornecidos começam como **rascunho**. Abrir uma página
pública não cria nem publica registos na base de dados.

Antes de publicar, a Chloé deve completar os pontos em aberto, retirar as
notas de preparação e validar os textos, incluindo as traduções. Enquanto
não houver secções publicadas, a página apresenta informação em actualização
e o contacto da organização. Não é apresentada como regulamento definitivo.

As minutas não certificam conformidade com o RGPD. Em particular, falta
confirmar os prazos efectivos de conservação e eliminação, contratos e locais
de tratamento dos fornecedores, garantias de transferências internacionais,
uso de fotografias, canal de privacidade e informação de consumo obrigatória.
Não se deve publicar uma descrição de medidas que ainda não estão implementadas.

## Informação do festival a rever

O site oficial anuncia a sexta edição em 23–29 de agosto de 2027. As FAQ
existentes dão uma base para as secções mas não substituem o regulamento.
Há referências contraditórias à abertura de portas (manhã/12h). Rever também
as condições de acompanhamento de menores, assistência, animais de assistência,
reembolso de bilhetes, eventual saldo cashless e normas de segurança.

O antigo endereço `/index.php/regras-e-normas-de-funcionamento/` redirecciona
para `/infos/rules`; o link das FAQ foi actualizado.

## Como funcionam as línguas

Português usa o URL sem prefixo; inglês, francês e espanhol usam `/en/`,
`/fr/` e `/es/`. Os botões são PT, EN, FR e ES. A escolha manual é guardada
num cookie de preferência durante um ano e tem prioridade na próxima entrada.

Na página inicial, sem preferência guardada, o país aproximado do IP é
consultado localmente na base DB-IP Lite. Não é pedido GPS e o IP não é enviado
para uma API de localização. Países sem idioma suportado usam inglês. Sem país
disponível, usa-se o primeiro idioma do navegador se suportado, ou inglês.
Localização por IP é aproximada e pode corresponder à localização de uma VPN.
A base de países deve ser actualizada durante a manutenção.

A Chloé escreve uma única versão do conteúdo. Os campos manuais de inglês e
francês foram retirados de Conteúdos; os dados antigos são preservados.
A tarefa «Build public translations» do GitHub verifica alterações de meia em
meia hora e gera as versões EN, FR e ES com LibreTranslate. São traduzidos
somente textos publicados e mensagens públicas do site. Rascunhos, encomendas,
dados pessoais dos compradores, credenciais e ecrãs administrativos ficam fora.

Os nomes de navegação e áreas de identidade inglesa estão protegidos em
`translation_terms.py`: The Experience, Tickets, Participate, Dub Temple,
River Station e os restantes termos dessa lista. São reconstruídos exactamente,
sem passarem pelo motor. Textos explicativos são traduzidos normalmente.

As traduções são guardadas na branch `site-translations`, que não activa deploys
do website. O site actualiza a cópia em memória de cinco em cinco minutos.
O conteúdo original é publicado imediatamente; as traduções podem levar mais
de meia hora, consoante a fila e o tempo de processamento do GitHub. Uma alteração
de texto nunca reutiliza a tradução de uma versão antiga desse texto.
Durante uma falha conservam-se as traduções carregadas e os catálogos existentes.
Na ausência da tradução pedida, tenta-se inglês e, em último recurso, o original.

**Custo adicional configurado: zero.** Esta solução depende de o repositório
continuar público e de usar apenas executores padrão gratuitos. Não cria um
servidor, não usa uma API paga, não guarda modelos em caches ou artefactos do
Actions e não activa executores de maiores dimensões. As tarefas têm um limite
de 55 minutos. A gratuidade não significa capacidade ou disponibilidade ilimitada.
Se o repositório passar a privado, o workflow recusa executar. As tarefas
agendadas podem atrasar e o GitHub pode desactivá-las após 60 dias sem actividade
no repositório público; nesse caso é necessário reactivá-las. Uma execução manual
pode ser iniciada em GitHub → Actions → Build public translations → Run workflow.

As traduções automáticas de regras, privacidade e termos devem ser revistas pela
organização, tal como o texto de origem. A administração e a área de cliente
mantêm a sua interface própria em português; os emails transaccionais existentes
mantêm o seu mecanismo separado. Não é apresentada uma certificação RGPD.

## Referências utilizadas

- Festival: https://himdubfestival.com/ e https://himdubfestival.com/infos/faq
- RGPD: https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679
- Direitos: https://www.cnpd.pt/cidadaos/direitos/
- Consentimento: https://www.cnpd.pt/organizacoes/areas-tematicas/consentimento/
- Contratos à distância: https://diariodarepublica.pt/dr/detalhe/decreto-lei/24-2014-572450
- Ícones locais: Simple Icons, CC0; atribuição em `templates/partials/icons/NOTICE.txt`.
- Caveat é servido localmente; licença SIL OFL junto ao ficheiro em `static/fonts/`.

- LibreTranslate: https://docs.libretranslate.com/
- GitHub Actions gratuito: https://docs.github.com/en/billing/concepts/product-billing/github-actions
- DB-IP Lite: https://db-ip.com/db/download/ip-to-country-lite
