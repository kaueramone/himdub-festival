# Galeria única

Administrar em **Galeria**. A opção Galeria em Conteúdos e as ligações antigas do editor redireccionam para esta área. O menu público abre `/gallery`, com capas Polaroid que abrem os álbuns internos.

Os antigos blocos da Chloé são migrados de forma idempotente para álbuns, preservando título, texto, capa, ordem, publicação, ligação de origem e créditos @perfil. Os blocos originais permanecem na base de dados como arquivo não publicado e não voltam a aparecer no website.

## Importação

1. Criar o álbum, definir fotógrafo e ligação de crédito e guardar.
2. Colar a ligação pública da pasta Google Drive. São enumerados os ficheiros de todas as subpastas, sem o antigo limite de 50 da vista principal do Drive. São aceites JPEG, PNG e WebP.
3. Para Pixieset, basta colar a ligação pública da galeria. Um processo no GitHub Actions abre a galeria pública, percorre as fotografias e produz a lista de imagens; o Render copia-as depois para o Bunny. Não são enviadas credenciais de produção para o processo. Se a origem exigir login/PIN ou bloquear a leitura pública, o painel indica o impedimento. A descarga oficial ZIP continua disponível como alternativa técnica. O painel mostra o requisito sem contornar as protecções.
4. Acompanhar o progresso e rever as fotografias/créditos antes de publicar. Links ZIP expirados precisam de ser renovados. O trabalho retoma ficheiros já copiados sem duplicar a mesma origem.

O importador funciona em segundo plano, com um trabalhador por processo Gunicorn, fila e progresso na base de dados. Copia imagens para o Bunny existente, optimiza para WebP com dimensão máxima 2400px e remove os metadados (incluindo GPS). O crédito Artist/Copyright é lido antes desta remoção quando não foi indicado um fotógrafo. Nomes captados automaticamente precisam de revisão: metadados podem estar vazios ou conter informação antiga.

Limites por importação: 5000 fotografias, até 100 subpastas, 30MB/50MP por original, ZIP até 2GB, apenas serviços HTTPS permitidos. Sem novos serviços pagos ou credenciais de visitantes; aplica-se o armazenamento/tráfego do Bunny já contratado.

## Eliminar e recuperar

Eliminar um álbum retira-o imediatamente do website e cancela importações em fila. Fica na reciclagem com as fotografias preservadas. Recuperar cria um rascunho, que precisa de nova publicação. Não apaga ficheiros do Bunny, pois podem ser reutilizados e a eliminação é reversível. Apenas Administradores podem gerir, importar, eliminar ou recuperar; Staff não tem acesso.

A descoberta Pixieset corre em runners públicos gratuitos do GitHub, com consulta programada a cada dez minutos (sujeita a atrasos do GitHub). Apenas as ligações de galerias públicas são disponibilizadas ao processo; o feed não contém textos editoriais, nomes de compradores, tokens de descarga ou dados financeiros. A lista de imagens públicas é publicada no ramo gallery-discovery. Não usar ligações com credenciais ou galerias confidenciais.
