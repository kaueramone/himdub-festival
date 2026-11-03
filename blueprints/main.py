"""Paginas de conteudo do site (tudo o que nao e loja/carrinho)."""

from flask import Blueprint, render_template, request, redirect, flash, abort
from flask_babel import gettext as _
from i18n import localized_url_for as url_for

main_bp = Blueprint("main", __name__)

# Fotos de cada área (ver templates/area_detail.html) -- ficheiros em
# static/img/areas/, ja redimensionados para a web. So as areas listadas
# aqui tem pagina propria; as restantes ficam so como cartao simples em
# /line-up (ver lineup(), mais abaixo).
AREA_DETAIL_IMAGES = {
    "dub-temple": ["dub-temple-1.jpg", "dub-temple-2.jpg", "dub-temple-3.jpg"],
    "river-station": ["river-station-1.jpg", "river-station-2.jpg", "river-station-3.jpg"],
    "roots-garden": ["roots-garden-1.jpg", "roots-garden-2.jpg"],
    "workshops": ["workshops-1.jpg", "workshops-2.jpg", "workshops-3.jpg"],
    "healing-area": ["healing-area-1.jpg", "healing-area-2.jpg", "healing-area-3.jpg"],
    "lectures": ["lectures-1.jpg", "lectures-2.jpg"],
}


@main_bp.route("/")
def home():
    return render_template("home.html")


@main_bp.route("/the-festival")
def festival():
    return render_template("festival.html")


@main_bp.route("/line-up")
def lineup():
    areas = [
        {"name": "Dub Temple", "slug": "dub-temple", "desc": _("O palco noturno dos heavyweights, com curadoria do Nomad Embassy Sound System.")},
        {"name": "River Station", "slug": "river-station", "desc": _("Heavy pressure junto ao rio, o palco da praia.")},
        {"name": "Roots Garden", "slug": "roots-garden", "desc": _("O canto rootikal, sessões madrugadoras.")},
        {"name": "Lectures", "slug": "lectures", "desc": _("Conferências e palestras sobre temas importantes.")},
        {"name": "Kids Area", "slug": None, "desc": _("Um espaço dedicado às famílias e aos mais pequenos.")},
        {"name": "Workshops", "slug": "workshops", "desc": _("Artesanato, arte, DIY e muito mais.")},
        {"name": "Healing Area", "slug": "healing-area", "desc": _("Terapias, massagens, yoga e bem-estar geral.")},
    ]
    return render_template("lineup.html", areas=areas)


@main_bp.route("/line-up/<slug>")
def area_detail(slug):
    """Pagina individual de cada area do recinto (Dub Temple, River
    Station, etc.) -- recupera o conteudo (texto + fotos) que existia no
    site antigo em WordPress, agora aqui. So existem paginas para as areas
    listadas em AREA_DETAIL_IMAGES; as outras (ainda sem fotos/texto
    fornecidos) ficam so como cartao em /line-up, sem link."""
    names = {
        "dub-temple": "Dub Temple",
        "river-station": "River Station",
        "roots-garden": "Roots Garden",
        "workshops": "Workshops",
        "healing-area": "Healing Area",
        "lectures": "Lectures",
    }
    texts = {
        "dub-temple": _(
            "Esta é a área principal do festival. Com curadoria do Nomad Embassy "
            "Sound System, é o espaço onde atuam os artistas principais. O Dub "
            "Temple está rodeado por vegetação natural e recebe um momento de "
            "celebração onde a música e a dança se encontram como o sol e a lua."
        ),
        "river-station": _(
            "Um dos lugares mais encantados do festival, situado à beira do Rio "
            "Côa, numa praia de areia, protegido pela sombra das árvores — uma "
            "viagem direta a Sião!"
        ),
        "roots-garden": _(
            "O Roots Garden, o espaço dedicado às Roots. Perto do campismo, "
            "acorda ao ar livre ao som doce do Mindel Reggae Sound System."
        ),
        "workshops": _(
            "Esta área é dedicada à aprendizagem e ao conhecimento através da "
            "prática de oficinas. De escultura em madeira a cerâmica, "
            "construções em adobe, produção do teu próprio bálsamo de óleos "
            "essenciais, entre outras."
        ),
        "healing-area": _(
            "Facilitada por profissionais da área, é um espaço seguro onde podes "
            "deixar as tuas emoções fluir, libertar, expandir, abraçar, "
            "fortalecer-te… Descobre diferentes técnicas e métodos orientados "
            "para o autoconhecimento e a incorporação (embodiment)."
        ),
        "lectures": _(
            "Este é o espaço dedicado a palestras e debates. O objetivo deste "
            "espaço é promover a interação cultural, permitindo a expressão de "
            "todos os aspetos e componentes culturais envolvidos (Rastafari, "
            "Dub, Permacultura, Som, Terapia e Bem-Estar, Cultura Local)."
        ),
    }
    # Só a Healing Area tem citação no site antigo -- as outras ficam None.
    quotes = {
        "healing-area": (
            _("A tua verdade encontra-se em tudo o que permanece escondido."),
            "Brianna Pastor",
        ),
    }
    if slug not in names:
        abort(404)
    quote = quotes.get(slug)
    return render_template(
        "area_detail.html",
        name=names[slug],
        text=texts[slug],
        images=AREA_DETAIL_IMAGES.get(slug, []),
        quote=quote[0] if quote else None,
        quote_author=quote[1] if quote else None,
    )


@main_bp.route("/participate")
def participate():
    ways = [
        {"title": _("Workshops"), "desc": _("Tens skills, experiência e know-how? Partilha-os connosco em oficinas de produção musical, construção de instrumentos e artesanato."), "status": "closed"},
        {"title": _("Healing"), "desc": _("Procuramos terapeutas, dançarinos e bodyworkers para oferecer um caminho regenerativo de apoio aos participantes."), "status": "closed"},
        {"title": _("Massagistas"), "desc": _("Profissionais de toque terapêutico para proporcionar restauro profundo aos participantes."), "status": "closed"},
        {"title": _("Mercado Artesanal"), "desc": _("Criadores e artesãos com cerâmica, têxteis e cosméticos naturais."), "status": "closed"},
        {"title": _("Voluntários"), "desc": _("\"O primeiro sorriso que as pessoas veem quando chegam.\" Essenciais para o festival acontecer."), "status": "closed"},
    ]
    return render_template("participate.html", ways=ways)


@main_bp.route("/ambassadors")
def ambassadors():
    return render_template("ambassadors.html")


@main_bp.route("/the-map")
def venue_map():
    return render_template("venue_map.html")


@main_bp.route("/infos/faq")
def faq():
    faqs = [
        (_("Quando é o festival?"), _("De 23 a 29 de agosto de 2027, com abertura ao público a 23 durante a manhã.")),
        (_("Onde é o festival?"), _("Praia Fluvial de Rapoula do Côa, Sabugal, distrito da Guarda, a cerca de 20km da fronteira com Espanha.")),
        (_("A que horas abrem as portas?"), _("As portas abrem ao meio-dia (12h) no dia 23 de agosto.")),
        (_("Há restrição de idade?"), _("Não - o festival é para todas as idades e é family-friendly.")),
        (_("Que tipos de bilhete existem?"), _("General, Family, Weekend, Accessibility e Ambassador.")),
        (_("Posso comprar bilhete à entrada?"), _("Sim, mas o estacionamento de caravanas é limitado - recomendamos comprar com antecedência.")),
        (_("Há reembolsos?"), _("Não há reembolso de bilhetes, exceto em caso de cancelamento do festival, morte ou hospitalização.")),
        (_("As crianças precisam de bilhete?"), _("Não - entrada gratuita para menores de 16 anos.")),
        (_("Posso revender o meu bilhete?"), _("Sim, fica atento -- vamos partilhar mais informação sobre isso nas próximas semanas.")),
        (_("O campismo está incluído?"), _("Sim, o campismo é gratuito com o bilhete do festival.")),
        (_("Posso levar caravana?"), _("Sim, há um parque de caravanas dedicado, disponível para compra.")),
        (_("Há duches e casas de banho?"), _("Sim, tanto na zona de campismo como no parque de caravanas.")),
        (_("Posso levar comida e bebida?"), _("Bebidas alcoólicas apenas na zona de campismo/caravanas. Comida e bebidas não alcoólicas podem ser levadas para todo o recinto.")),
        (_("Há água disponível?"), _("Sim, no campismo, no parque de caravanas e no recinto do festival.")),
        (_("Posso levar o meu cão?"), _("Não, não são permitidos cães no festival.")),
        (_("O evento é cashless?"), _("Sim - usamos pulseiras para os pagamentos. Todos os carregamentos são finais e não reembolsáveis.")),
        (_("Devo levar fato de banho?"), _("Sem dúvida - o festival é construído à volta de um lugar de rio maravilhoso.")),
    ]
    return render_template("infos/faq.html", faqs=faqs)


@main_bp.route("/infos/partners")
def partners():
    return render_template("infos/partners.html")


@main_bp.route("/infos/sustainability")
def sustainability():
    eco_cards = [
        {"color": "#d97757", "title": _("Kit de Boas-vindas"), "desc": _("Um kit de higiene ecológico é oferecido à entrada do festival, com um sabonete 100% natural e artesanal, pó dentífrico e um saquinho de sementes.")},
        {"color": "#c9b93a", "title": _("Casas de Banho Secas"), "desc": _("Sem água, sem químicos. Serrim substitui o autoclismo, poupando milhares de litros de água, e todos os resíduos são compostados de volta à terra.")},
        {"color": "#5aa8c9", "title": _("Restos de Comida"), "desc": _("Os restos de comida dos nossos restaurantes e da cozinha da equipa são doados a pastores locais para alimentar os seus animais, fechando o ciclo do prato ao pasto.")},
        {"color": "#3f6b3f", "title": _("Produtos Locais"), "desc": _("Priorizamos fornecedores e produtores da região em toda a cadeia de alimentação e artesanato.")},
        {"color": "#3a6ea8", "title": _("Respeito pelo Rio"), "desc": _("O recinto é pensado para minimizar o impacto na praia fluvial e no ecossistema envolvente.")},
        {"color": "#6b5a44", "title": _("Comunidade"), "desc": _("Colaboração ativa com associações e produtores locais, como Uprising e Nomad.")},
    ]
    return render_template("infos/sustainability.html", eco_cards=eco_cards)


@main_bp.route("/infos/contact")
def contact():
    return render_template("infos/contact.html")


@main_bp.route("/newsletter", methods=["POST"])
def newsletter():
    email = request.form.get("email", "").strip()
    if email:
        flash(_("As inscrições na newsletter ainda não estão disponíveis. Tenta novamente em breve."), "error")
    else:
        flash(_("Introduz um email válido."), "error")
    return redirect(url_for("main.home"))
