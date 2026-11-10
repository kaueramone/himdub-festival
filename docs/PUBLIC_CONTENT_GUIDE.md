# Conteúdos públicos, traduções e páginas legais

## Edição das novas páginas

Em Administração → Conteúdos, escolher Regras e normas, Privacidade,
Termos e condições ou Cookies. Cada secção tem título, texto, ordem,
traduções e a opção de publicação. É possível editar e duplicar secções.
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

Português usa o URL sem prefixo; inglês e francês usam `/en/` e `/fr/`.
Flask-Babel carrega traduções manuais dos catálogos `.po`, compilados em `.mo`.
Não existe serviço de tradução automática nem custo de API de tradução.
Conteúdos editáveis têm campos próprios de inglês e francês; na ausência de
uma tradução, aparece o português. Alterar o português não actualiza uma
tradução antiga: é necessário revê-la. Administração e área de cliente
mantêm a interface própria em português.

Os novos rótulos do rodapé e das páginas legais estão traduzidos. As minutas
legais são fornecidas em português para validação, antes da tradução final.

## Referências utilizadas

- Festival: https://himdubfestival.com/ e https://himdubfestival.com/infos/faq
- RGPD: https://eur-lex.europa.eu/legal-content/PT/TXT/?uri=CELEX:32016R0679
- Direitos: https://www.cnpd.pt/cidadaos/direitos/
- Consentimento: https://www.cnpd.pt/organizacoes/areas-tematicas/consentimento/
- Contratos à distância: https://diariodarepublica.pt/dr/detalhe/decreto-lei/24-2014-572450
- Ícones locais: Simple Icons, CC0; atribuição em `templates/partials/icons/NOTICE.txt`.
- Caveat é servido localmente; licença SIL OFL junto ao ficheiro em `static/fonts/`.
