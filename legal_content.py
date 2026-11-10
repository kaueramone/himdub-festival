"""Legal drafts for organiser review; no draft is published automatically.

Sources: festival FAQ; GDPR arts. 5, 6, 12–22, 28, 44–49; CNPD guidance;
Decree-Law 24/2014; actual website and supplier inventory.
"""
LEGAL_PAGES = {
    'rules': ('Regras e normas de funcionamento', '/infos/rules'),
    'privacy': ('Política de privacidade', '/infos/privacy'),
    'terms': ('Termos e condições', '/infos/terms'),
    'cookies': ('Política de cookies', '/infos/cookies'),
}

DRAFTS = {
    'rules': [
        ('A sexta edição', 'O HIM Dub Festival anuncia a 6.ª edição para 23 a 29 de agosto de 2027, na Praia Fluvial de Rapoula do Côa, Sabugal. Confirmar os horários de abertura: as FAQ anteriores referem tanto a manhã como as 12h.'),
        ('Bilhetes e acesso', 'Guarda o bilhete e mantém o código QR privado. O pagamento precisa de estar confirmado para emitir o bilhete. Uma cópia não cria um novo direito de entrada. A organização deve completar as regras de identificação, troca de pulseira e reentrada.'),
        ('Crianças, acessibilidade e famílias', 'As FAQ apresentam o festival como um evento para todas as idades e indicam entrada gratuita para menores de 16 anos. Confirmar acompanhamento, documentos, bilhete de família, acessibilidade e condições dos acompanhantes antes de publicar.'),
        ('Campismo, caravanas e glamping', 'A informação publicada indica campismo incluído no bilhete e passe próprio para o parque de caravanas. O glamping é reservado com o parceiro externo Lifes Work Glamping, sujeito às suas condições. Completar os horários e regras de cada zona.'),
        ('Comida, bebidas e animais', 'As FAQ actuais permitem alimentos e bebidas sem álcool no recinto e limitam bebidas alcoólicas ao campismo e caravanas. Referem também a proibição de cães. Rever as excepções legalmente aplicáveis, incluindo animais de assistência, antes da publicação.'),
        ('Segurança e funcionamento do recinto', 'A completar pela organização: objectos interditos, prevenção de incêndios, acesso ao rio, primeiros socorros, circulação de veículos, ruído, resíduos, emergência e regras de convivência. Validar esta secção com o plano de segurança da edição.'),
        ('Cancelamentos, reembolsos e cashless', 'As FAQ anteriores mencionam excepções de reembolso por cancelamento do festival, morte ou hospitalização. Confirmar a política da edição, os prazos, o contacto e comprovativos mínimos, sem afastar direitos legais. Rever separadamente os carregamentos e a devolução de saldo cashless. Não reutilizar exclusões gerais de reembolso sem validação.'),
    ],
    'privacy': [
        ('Responsável e contacto', 'Responsável pelo tratamento: DECIBEL COSTUME LDA, NIPC 517567024, CAM PTE NOVA SN — QTA PTE NOVA — Fornos de Algodres, 6370-206, Portugal. Email: decibelcostume@gmail.com. Confirmar o canal de privacidade e a morada antes da publicação.'),
        ('Dados, finalidades e fundamentos', 'A compra utiliza nome, email, país, NIF opcional e dados necessários dos titulares dos bilhetes. A gestão da encomenda, emissão, entrada e apoio assenta no contrato ou em diligências pré-contratuais; a informação fiscal, no cumprimento de obrigações legais. A newsletter utiliza consentimento separado e confirmação por email. A segurança e prevenção de fraude podem assentar em interesses legítimos, sujeitos a avaliação de necessidade e proporcionalidade. Dados de menores e acompanhantes devem limitar-se ao estritamente necessário.'),
        ('Área de cliente e segurança', 'A área de cliente usa o email e links de acesso com validade limitada. A equipa administrativa usa credenciais individuais e autenticação reforçada. São mantidos registos técnicos de segurança e acesso. O website usa HTTPS e valida a confirmação do pagamento antes da emissão. Não foram identificadas decisões automatizadas de elegibilidade com efeitos jurídicos no checkout analisado.'),
        ('Destinatários e fornecedores', 'O pagamento é processado por Teya ou Stripe, consoante a configuração da organização. O website não guarda os dados completos do cartão. Render suporta o alojamento e a base de dados; Resend, os emails e contactos; Bunny, os ficheiros multimédia; GitHub, o código. O parceiro de glamping trata as reservas externas segundo a sua própria política. Confirmar os contratos, funções de responsável/subcontratante e acessos de cada fornecedor.'),
        ('Transferências internacionais', 'Alguns fornecedores podem tratar dados fora do Espaço Económico Europeu. Identificar os locais efectivos de tratamento e as garantias aplicáveis — decisão de adequação ou garantias apropriadas, como cláusulas contratuais-tipo e medidas complementares quando necessárias. O titular pode solicitar informação e cópia das garantias pelo contacto de privacidade. Não afirmar que todos os dados permanecem na União Europeia sem confirmar a cadeia de fornecedores.'),
        ('Conservação', 'A organização deve fixar e aplicar prazos ou critérios concretos para encomendas, documentos fiscais, entradas, apoio, contas, segurança e prova de consentimento. A newsletter mantém-se enquanto houver consentimento; após cancelamento, conservar apenas o mínimo necessário para respeitar a oposição e demonstrar o cumprimento das obrigações. A expiração de um link não elimina automaticamente o registo da base de dados. Completar os prazos e procedimentos de eliminação, incluindo cópias de segurança, antes de publicar.'),
        ('Direitos e reclamações', 'Podes pedir acesso, rectificação, apagamento, limitação, portabilidade ou oposição, quando aplicáveis, e retirar o consentimento sem afectar a licitude do tratamento anterior. Contacta o responsável; poderá ser necessária confirmação proporcional da identidade. A resposta é prestada, em regra, no prazo de um mês, com as extensões legalmente previstas. Podes reclamar à Comissão Nacional de Protecção de Dados, em www.cnpd.pt.'),
        ('Fotografias, menores e actualizações', 'A organização deve completar a informação sobre fotografias e vídeos do público, finalidades, fundamentos e canais para exercício dos direitos. A compra não deve presumir um consentimento geral para todas as utilizações da imagem. Dados de crianças devem ser fornecidos pelo responsável legal quando necessário. Rever esta política quando mudarem funcionalidades ou fornecedores.'),
    ],
    'terms': [
        ('Identificação e âmbito', 'Website de DECIBEL COSTUME LDA, NIPC 517567024, CAM PTE NOVA SN — QTA PTE NOVA — Fornos de Algodres, 6370-206, Portugal. Contacto: decibelcostume@gmail.com. Identificar a edição e os serviços abrangidos. Merchandise com envio postal e shuttles necessitam de condições específicas quando forem disponibilizados.'),
        ('Preço, compra e pagamento', 'O checkout apresenta produtos, quantidades, taxa de processamento e total em euros antes da confirmação. O pagamento decorre na página segura do fornecedor configurado. O bilhete só é emitido após confirmação válida. Completar a informação fiscal, facturação e condições comerciais aplicáveis; não presumir a isenção de IVA do promotor a partir da isenção do prestador de desenvolvimento.'),
        ('Entrega e utilização', 'Os bilhetes digitais são enviados para os endereços associados à compra após confirmação e podem ser consultados na área de cliente. Verifica os dados antes de pagar. Mantém o QR privado e contacta a organização se houver erro. A troca de titular depende das regras e funcionalidades efectivamente disponíveis; não pressupõe revenda segura numa plataforma externa.'),
        ('Cancelamentos e direitos do consumidor', 'Inserir a política validada de cancelamento, adiamento, alterações relevantes e reembolso, preservando os direitos legais. Bilhetes de lazer com data ou período específicos podem enquadrar-se no artigo 17.º, n.º 1, alínea k), do Decreto-Lei n.º 24/2014. Esta excepção à livre resolução não elimina todos os direitos a reembolso e não deve ser aplicada indiscriminadamente a merchandise enviado por correio.'),
        ('Parceiros, apoio e reclamações', 'Reservas externas são contratadas com o parceiro identificado e seguem as suas condições, sem afastar responsabilidades próprias da organização. Apoio pela área de cliente ou decibelcostume@gmail.com. Confirmar o acesso ao Livro de Reclamações Electrónico e identificar a entidade de resolução alternativa de litígios competente, quando aplicável. Não indicar a antiga plataforma europeia de resolução de litígios em linha como canal activo. Validar a versão final antes de publicar.'),
    ],
    'cookies': [
        ('Sessão e funções necessárias', 'O website utiliza um cookie de sessão para carrinho, autenticação e protecção dos formulários. É configurado com HttpOnly, SameSite=Lax e Secure quando servido por HTTPS. Sessões permanentes administrativas: 8 horas; sessões de cliente: 12 horas. As sessões de navegação podem terminar com o fecho do navegador. Confirmar a duração efectiva das restantes sessões e fornecedores.'),
        ('Preferências no navegador', 'O sessionStorage guarda temporariamente o estado e posição da música e a transição entre páginas. O localStorage evita repetir a indicação de navegação por gestos. Estes valores não são usados pelo website para publicidade. Rever nomes e durações quando o código mudar.'),
        ('Terceiros e controlo', 'Teya ou Stripe podem utilizar mecanismos próprios no ambiente de pagamento. Bunny serve os conteúdos multimédia. No código analisado não foram identificados Google Analytics, Meta Pixel ou cookies de publicidade. Antes de introduzir tecnologias não necessárias, informar e obter consentimento prévio adequado, com possibilidade de rejeitar e retirar a escolha. Podes gerir os cookies no navegador; bloquear os necessários pode impedir compras e autenticação.'),
    ],
}

LEGAL_DEFAULT_CONTENT = {
    page: [dict(key=f'{page}:{i}',kind='legal',title=title,text=text,
                published=False,sort_order=i*10)
           for i,(title,text) in enumerate(sections)]
    for page,sections in DRAFTS.items()
}
