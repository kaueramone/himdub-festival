"""Popula a base de dados com os produtos iniciais (bilhetes, glamping, merch).

Os precos e descricoes foram deixados com valores de exemplo, porque o site
antigo nao os publicava. Ajusta os PRICE_CENTS e textos abaixo antes de ires
para producao - e o unico sitio onde precisas de mexer para isso.

Corre com:
    python seed.py
"""

from app import create_app
from extensions import db
from models import Product

PRODUCTS = [
    # --- Bilhetes -----------------------------------------------------
    dict(
        slug="bilhete-geral",
        category="ticket",
        name="Bilhete Geral",
        name_en="General Ticket",
        name_fr="Billet General",
        short_description="Acesso completo aos 7 dias de festival (23 a 29 de agosto de 2027).",
        short_description_en="Full access to all 7 festival days (August 23 to 29, 2027).",
        short_description_fr="Accès complet aux 7 jours du festival (23 au 29 août 2027).",
        description=(
            "O bilhete geral dá o acesso a todas as áreas do festival durante os 7 dias, "
            "incluindo o campismo gratuito. Não inclui alojamento em glamping."
        ),
        description_en=(
            "The general ticket gives access to all festival areas for the full 7 days, "
            "including free camping. Glamping accommodation not included."
        ),
        description_fr=(
            "Le billet général donne accès à toutes les zones du festival pendant les 7 jours, "
            "camping gratuit inclus. L'hébergement en glamping n'est pas inclus."
        ),
        price_cents=14000,
        image_class="ph-sun",
        sort_order=1,
    ),
    dict(
        slug="bilhete-fim-de-semana",
        category="ticket",
        name="Bilhete Fim de Semana",
        name_en="Weekend Ticket",
        name_fr="Billet Week-end",
        short_description="Acesso de sexta a domingo.",
        short_description_en="Access from Friday to Sunday.",
        short_description_fr="Accès du vendredi au dimanche.",
        description="Ideal para quem só pode ir ao festival no fim de semana. Inclui campismo.",
        description_en="Ideal for those who can only attend the festival on the weekend. Camping included.",
        description_fr="Idéal pour celles et ceux qui ne peuvent venir au festival que le week-end. Camping inclus.",
        # Valor de referência/fallback só (ver Product.effective_price_cents
        # em models.py) -- o preço a sério vem de TICKET_BATCHES em
        # batches.py, que agora também tem lotes para este bilhete (pedido
        # do João). Mantido igual ao preço do 1º lote (Early Bird) para não
        # ficar enganador para quem ler o código.
        price_cents=17000,
        image_class="ph-river",
        sort_order=2,
    ),
    dict(
        slug="bilhete-familia",
        category="ticket",
        name="Bilhete Família",
        name_en="Family Ticket",
        name_fr="Billet Famille",
        short_description="7 dias de festival + dia de boas-vindas. 2 adultos + crianças, entrada gratuita para menores de 16 anos.",
        short_description_en="7 days of festival + Welcome day. 2 Adults + Kids, free entrance for those under 16.",
        short_description_fr="7 jours de festival + jour d'accueil. 2 adultes + enfants, entrée gratuite pour les moins de 16 ans.",
        description=(
            "Pensado para famílias: 2 adultos com acesso total de 7 dias mais o dia de "
            "boas-vindas. A entrada é sempre gratuita para menores de 16 anos. Este "
            "bilhete fica sempre 20€ mais barato do que comprar 2 Full Pass separados."
        ),
        description_en=(
            "Designed for families: 2 adults with full access to the 7 days plus the "
            "welcome day. Kids under 16 do not pay entrance. This ticket is always 20€ "
            "less expensive than 2x Full Pass."
        ),
        description_fr=(
            "Pensé pour les familles : 2 adultes avec accès complet aux 7 jours plus le "
            "jour d'accueil. L'entrée est toujours gratuite pour les moins de 16 ans. Ce "
            "billet coûte toujours 20€ de moins que 2x Pass Complet."
        ),
        # Idem (ver comentário no Bilhete Fim de Semana, acima) -- preço a
        # sério vem de TICKET_BATCHES.
        price_cents=34000,
        image_class="ph-family",
        sort_order=3,
    ),
    dict(
        slug="bilhete-acessibilidade",
        category="ticket",
        name="Bilhete Acessibilidade",
        name_en="Accessibility Ticket",
        name_fr="Billet Accessibilité",
        short_description="Para pessoas com mobilidade reduzida. 7 dias de festival + dia de boas-vindas. Inclui acompanhante gratuito.",
        short_description_en="For people with reduced mobility. 7 days of festival + Welcome day. Includes a free companion pass.",
        short_description_fr="Pour les personnes à mobilité réduite. 7 jours de festival + jour d'accueil. Accompagnateur gratuit inclus.",
        description=(
            "Pensado para pessoas com mobilidade reduzida: acesso total de 7 dias + dia "
            "de boas-vindas, com acompanhante gratuito. O festival ainda não está 100% "
            "acessível."
        ),
        description_en=(
            "Designed for people with reduced mobility: full access to the 7 days plus "
            "the welcome day, with a free companion. The festival isn't 100% accessible "
            "yet."
        ),
        description_fr=(
            "Pensé pour les personnes à mobilité réduite : accès complet aux 7 jours plus "
            "le jour d'accueil, avec un accompagnateur gratuit. Le festival n'est pas "
            "encore 100% accessible."
        ),
        price_cents=14000,
        image_class="ph-leaf",
        sort_order=4,
    ),
    dict(
        slug="passe-caravan",
        category="ticket",
        name="Passe de Caravana",
        name_en="Caravan Pass",
        name_fr="Pass Caravane",
        short_description="Lugar reservado no parque de caravanas (vagas limitadas).",
        short_description_en="Reserved spot in the caravan park (limited spaces).",
        short_description_fr="Place réservée dans le parc à caravanes (places limitées).",
        description="Complemento ao bilhete geral - garante o teu lugar no parque de caravanas.",
        description_en="A complement to the general ticket — secure your spot in the caravan park.",
        description_fr="Un complément au billet général — réserve ta place dans le parc à caravanes.",
        price_cents=8000,
        image_class="ph-mountain",
        sort_order=5,
    ),
    dict(
        slug="full-pass-caravan",
        category="ticket",
        name="Full Pass + Passe de Caravana",
        name_en="Full Pass + Caravan Pass",
        name_fr="Pass Complet + Pass Caravane",
        short_description="Bilhete geral + lugar no parque de caravanas, num só bilhete.",
        short_description_en="General ticket + a spot in the caravan park, in one single ticket.",
        short_description_fr="Billet général + place au parc à caravanes, en un seul billet.",
        description=(
            "O Full Pass e o Passe de Caravana num único bilhete/QR -- mais prático do "
            "que comprar os dois em separado. O preço acompanha sempre o lote atual do "
            "Full Pass (ver ticket geral)."
        ),
        description_en=(
            "The Full Pass and the Caravan Pass in a single ticket/QR -- simpler than "
            "buying both separately. The price always follows the Full Pass's current "
            "batch (see the general ticket)."
        ),
        description_fr=(
            "Le Pass Complet et le Pass Caravane en un seul billet/QR -- plus simple que "
            "de les acheter séparément. Le prix suit toujours le lot actuel du Pass "
            "Complet (voir le billet général)."
        ),
        # Preço de referência (1º lote: 140€ Full Pass + 80€ Caravan) -- o
        # preço cobrado a sério é sempre calculado a partir dos lotes (ver
        # batches.py Product.effective_price_cents), por isso este valor
        # nunca fica desatualizado em produção, só serve de fallback.
        price_cents=22000,
        image_class="ph-sun",
        sort_order=6,
    ),
    # --- Bilhetes de cortesia (emitidos so pela area de admin, nunca --
    # aparecem na loja publica -- ver blueprints/admin.py "Emitir bilhete"
    # e templates/tickets.html, que nao os lista). price_cents=0 porque
    # nunca passam pelo Stripe.
    dict(
        slug="vip-guest",
        category="ticket",
        name="VIP Guest",
        name_en="VIP Guest",
        name_fr="VIP Guest",
        short_description="Bilhete de cortesia para convidados VIP.",
        short_description_en="Courtesy ticket for VIP guests.",
        short_description_fr="Billet de courtoisie pour invités VIP.",
        description="Bilhete de cortesia, emitido manualmente pela organização.",
        description_en="Courtesy ticket, issued manually by the organisation.",
        description_fr="Billet de courtoisie, émis manuellement par l'organisation.",
        price_cents=0,
        image_class="ph-crown",
        is_active=False,
        sort_order=90,
    ),
    dict(
        slug="embaixador-argentina",
        category="ticket",
        name="Embaixador Argentina",
        name_en="Argentina Ambassador",
        name_fr="Ambassadeur Argentine",
        short_description="Bilhete de cortesia para embaixador do festival na Argentina.",
        short_description_en="Courtesy ticket for the festival's ambassador in Argentina.",
        short_description_fr="Billet de courtoisie pour l'ambassadeur du festival en Argentine.",
        description="Bilhete de cortesia, emitido manualmente pela organização.",
        description_en="Courtesy ticket, issued manually by the organisation.",
        description_fr="Billet de courtoisie, émis manuellement par l'organisation.",
        price_cents=0,
        image_class="ph-globe",
        is_active=False,
        sort_order=91,
    ),
    dict(
        slug="embaixador-burkina-faso",
        category="ticket",
        name="Embaixador Burkina Faso",
        name_en="Burkina Faso Ambassador",
        name_fr="Ambassadeur Burkina Faso",
        short_description="Bilhete de cortesia para embaixador do festival no Burkina Faso.",
        short_description_en="Courtesy ticket for the festival's ambassador in Burkina Faso.",
        short_description_fr="Billet de courtoisie pour l'ambassadeur du festival au Burkina Faso.",
        description="Bilhete de cortesia, emitido manualmente pela organização.",
        description_en="Courtesy ticket, issued manually by the organisation.",
        description_fr="Billet de courtoisie, émis manuellement par l'organisation.",
        price_cents=0,
        image_class="ph-globe",
        is_active=False,
        sort_order=92,
    ),
    dict(
        slug="embaixador-irlanda",
        category="ticket",
        name="Embaixador Irlanda",
        name_en="Ireland Ambassador",
        name_fr="Ambassadeur Irlande",
        short_description="Bilhete de cortesia para embaixador do festival na Irlanda.",
        short_description_en="Courtesy ticket for the festival's ambassador in Ireland.",
        short_description_fr="Billet de courtoisie pour l'ambassadeur du festival en Irlande.",
        description="Bilhete de cortesia, emitido manualmente pela organização.",
        description_en="Courtesy ticket, issued manually by the organisation.",
        description_fr="Billet de courtoisie, émis manuellement par l'organisation.",
        price_cents=0,
        image_class="ph-globe",
        is_active=False,
        sort_order=93,
    ),
    # --- Glamping -------------------------------------------------------
    dict(
        slug="glamping-comfort-double",
        category="glamping",
        name="Comfort Double",
        name_en="Comfort Double",
        name_fr="Comfort Double",
        short_description="Tenda equipada para 2 pessoas, 6 noites (23-29 ago 2027).",
        short_description_en="Equipped tent for 2 people, 6 nights (Aug 23-29, 2027).",
        short_description_fr="Tente équipée pour 2 personnes, 6 nuits (23-29 août 2027).",
        description="Interior confortável para uma boa noite de sono a dois, junto à natureza.",
        description_en="A comfortable interior for a good night's sleep for two, close to nature.",
        description_fr="Un intérieur confortable pour une bonne nuit de sommeil à deux, proche de la nature.",
        price_cents=35000,
        image_class="ph-tent",
        sort_order=1,
    ),
    dict(
        slug="glamping-comfort-quadruplet",
        category="glamping",
        name="Comfort Quadruplet",
        name_en="Comfort Quadruplet",
        name_fr="Comfort Quadruplet",
        short_description="Tenda equipada para 4 pessoas, 6 noites (23-29 ago 2027).",
        short_description_en="Equipped tent for 4 people, 6 nights (Aug 23-29, 2027).",
        short_description_fr="Tente équipée pour 4 personnes, 6 nuits (23-29 août 2027).",
        description="Perfeita para grupos de amigos ou família, com todo o conforto do glamping.",
        description_en="Perfect for groups of friends or family, with all the comfort of glamping.",
        description_fr="Parfaite pour un groupe d'amis ou en famille, avec tout le confort du glamping.",
        price_cents=60000,
        image_class="ph-tent-2",
        sort_order=2,
    ),
    dict(
        slug="glamping-luxury-double",
        category="glamping",
        name="Luxury Double Room",
        name_en="Luxury Double Room",
        name_fr="Chambre Double Luxe",
        short_description="Quarto de luxo para 2 pessoas, 6 noites (23-29 ago 2027).",
        short_description_en="Luxury room for 2 people, 6 nights (Aug 23-29, 2027).",
        short_description_fr="Chambre de luxe pour 2 personnes, 6 nuits (23-29 août 2027).",
        description="A opção mais confortável da Glamping Village, com acabamentos premium.",
        description_en="The most comfortable option in the Glamping Village, with premium finishes.",
        description_fr="L'option la plus confortable du Glamping Village, avec des finitions haut de gamme.",
        price_cents=55000,
        image_class="ph-tent-lux",
        sort_order=3,
    ),
    # --- Merchandise ------------------------------------------------------
    dict(
        slug="hoodie-music-is-the-most-high",
        category="merch",
        name='Hoodie "Music Is The Most High"',
        name_en='"Music Is The Most High" Hoodie',
        name_fr='Hoodie "Music Is The Most High"',
        short_description="Produzido localmente em Portugal. Handmade, handscreen, handcut.",
        short_description_en="Locally made in Portugal. Handmade, handscreen, handcut.",
        short_description_fr="Fabriqué localement au Portugal. Handmade, handscreen, handcut.",
        description="Hoodie serigrafado à mão, produção local e em pequena escala.",
        description_en="Hand-screen-printed hoodie, made locally in small batches.",
        description_fr="Hoodie sérigraphié à la main, production locale à petite échelle.",
        price_cents=4500,
        image_class="ph-hoodie",
        variants="S,M,L,XL",
        sort_order=1,
    ),
    dict(
        slug="tshirt-music-is-the-most-high",
        category="merch",
        name='T-shirt "Music Is The Most High"',
        name_en='"Music Is The Most High" T-shirt',
        name_fr='T-shirt "Music Is The Most High"',
        short_description="Produzida localmente em Portugal. Handmade, handscreen, handcut.",
        short_description_en="Locally made in Portugal. Handmade, handscreen, handcut.",
        short_description_fr="Fabriqué localement au Portugal. Handmade, handscreen, handcut.",
        description="T-shirt serigrafada à mão, produção local e em pequena escala.",
        description_en="Hand-screen-printed t-shirt, made locally in small batches.",
        description_fr="T-shirt sérigraphié à la main, production locale à petite échelle.",
        price_cents=2500,
        image_class="ph-tshirt",
        variants="S,M,L,XL",
        sort_order=2,
    ),
    dict(
        slug="tote-bag-him-dub",
        category="merch",
        name="Tote Bag HIM DUB",
        name_en="HIM DUB Tote Bag",
        name_fr="Tote Bag HIM DUB",
        short_description="Saco de tecido resistente com o logo do festival.",
        short_description_en="Sturdy fabric bag with the festival logo.",
        short_description_fr="Sac en tissu résistant avec le logo du festival.",
        description="O acessório perfeito para levar contigo tudo o que precisas no festival.",
        description_en="The perfect accessory to carry everything you need at the festival.",
        description_fr="L'accessoire parfait pour emporter tout ce dont tu as besoin au festival.",
        price_cents=1500,
        image_class="ph-tote",
        sort_order=3,
    ),
    dict(
        slug="ceramic-coffee-cup",
        category="merch",
        name="Caneca de Cerâmica",
        name_en="Ceramic Coffee Cup",
        name_fr="Tasse en Céramique",
        short_description="Caneca de cerâmica feita à mão.",
        short_description_en="Handmade ceramic coffee cup.",
        short_description_fr="Tasse en céramique faite à la main.",
        description="Peça única de cerâmica artesanal, produzida por criadores locais.",
        description_en="A one-of-a-kind handcrafted ceramic piece, made by local creators.",
        description_fr="Une pièce unique en céramique artisanale, produite par des créateurs locaux.",
        price_cents=1800,
        image_class="ph-cup",
        sort_order=4,
    ),
    dict(
        slug="eco-pocket-ashtray-biataki",
        category="merch",
        name="Cinzeiro de Bolso Eco – Biataki",
        name_en="Eco-Pocket Ashtray – Biataki",
        name_fr="Cendrier de Poche Éco – Biataki",
        short_description="Cinzeiro de bolso reutilizável e ecológico.",
        short_description_en="Reusable, eco-friendly pocket ashtray.",
        short_description_fr="Cendrier de poche réutilisable et écologique.",
        description="Uma forma prática e responsável de manter o festival limpo.",
        description_en="A practical, responsible way to keep the festival clean.",
        description_fr="Une façon pratique et responsable de garder le festival propre.",
        price_cents=1200,
        image_class="ph-ashtray",
        sort_order=5,
    ),
]


def seed():
    app = create_app()
    with app.app_context():
        db.create_all()
        for data in PRODUCTS:
            existing = Product.query.filter_by(slug=data["slug"]).first()
            if existing:
                for key, value in data.items():
                    setattr(existing, key, value)
            else:
                db.session.add(Product(**data))
        db.session.commit()
        print(f"OK - {len(PRODUCTS)} produtos verificados/criados na base de dados.")


if __name__ == "__main__":
    seed()
