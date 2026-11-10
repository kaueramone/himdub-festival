"""Paginas de conteudo do site (tudo o que nao e loja/carrinho)."""

from flask import Blueprint, render_template, request, redirect, flash, abort
from auto_translation import gettext as _
from i18n import localized_url_for as url_for

main_bp = Blueprint("main", __name__)


@main_bp.route('/infos/<document>')
def legal_page(document):
    from legal_content import LEGAL_PAGES
    if document not in LEGAL_PAGES:
        abort(404)
    return render_template('infos/legal.html', document=document,
                           document_title=LEGAL_PAGES[document][0])


@main_bp.route('/index.php/regras-e-normas-de-funcionamento/')
def legacy_rules():
    return redirect(url_for('main.legal_page', document='rules'), code=301)


@main_bp.route('/gallery')
def gallery():
    from models import GalleryAlbum
    return render_template('gallery.html', album=None,
        albums=GalleryAlbum.query.filter_by(published=True).order_by(GalleryAlbum.sort_order,GalleryAlbum.id.desc()).all())


@main_bp.route('/gallery/<int:album_id>')
def gallery_album(album_id):
    from models import GalleryAlbum
    album=GalleryAlbum.query.filter_by(id=album_id,published=True).first_or_404()
    return render_template('gallery.html',album=album)

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
    return render_template('participate.html')


@main_bp.route("/ambassadors")
def ambassadors():
    return render_template("ambassadors.html")


@main_bp.route("/the-map")
def venue_map():
    return render_template("venue_map.html")


@main_bp.route("/infos/faq")
def faq():
    return render_template('infos/faq.html')


@main_bp.route("/infos/partners")
def partners():
    return render_template("infos/partners.html")


@main_bp.route("/infos/sustainability")
def sustainability():
    return render_template('infos/sustainability.html')


@main_bp.route("/infos/contact")
def contact():
    return render_template("infos/contact.html")


@main_bp.route("/newsletter", methods=["POST"])
def newsletter():
    from newsletter import subscribe
    return subscribe()
