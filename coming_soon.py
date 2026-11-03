"""Página "brevemente" que bloqueia o site inteiro enquanto as vendas ainda
não abrem oficialmente (ver COMING_SOON_MODE em config.py).

Ao contrário do site_lock.py (que pede uma password num diálogo feio e
nativo do browser), isto mostra uma página bonita e informativa, sem deixar
entrar em mais nenhuma página do site -- pedido explícito do João para o
lançamento: "uma página informativa que não daria para entrar no site".

Liga-se e desliga-se só com a variável de ambiente COMING_SOON_MODE no
Render (ver config.py) -- não precisa de novo deploy nem de mexer em
código. Ficam sempre acessíveis, mesmo com isto ligado:
  - /webhooks/*  (o Stripe chama isto servidor-a-servidor, sem browser)
  - /admin       (para a equipa continuar a gerir encomendas)
  - /checkin     (para a equipa do check-in continuar a funcionar)
  - /static/*    (para a própria página "brevemente" ter estilos/imagens)
"""

from flask import current_app, request, render_template

_ALLOWED_PREFIXES = ("/webhooks/", "/admin", "/checkin", "/static/")


def check_coming_soon():
    """Chamado num @app.before_request -- devolve a página "brevemente" se o
    modo estiver ligado e o pedido não for a uma das exceções acima, ou
    None para deixar o pedido seguir normalmente."""
    if not current_app.config.get("COMING_SOON_MODE"):
        return None

    if request.path.startswith(_ALLOWED_PREFIXES) or request.path == "/healthz":
        return None

    return render_template("coming_soon.html")
