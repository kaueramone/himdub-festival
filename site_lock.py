"""Bloqueio temporário do site inteiro por password (HTTP Basic Auth).

Para usar antes do lançamento oficial: enquanto o site ainda está a testar
(chaves de teste do Stripe), não queremos que alguém que encontre o link
por acaso -- ex: a pesquisar "him dub" antes do domínio a sério estar
ligado -- vá parar aqui e consiga "comprar" um bilhete.

Ativa-se só definindo a variável de ambiente SITE_LOCK_PASSWORD (no .env
local, ou nas variáveis de ambiente do Render em produção). Vazio (o
valor por defeito) = bloqueio desligado, o site funciona normalmente sem
pedir nada -- por isso isto nunca "acontece sozinho", é preciso ligares
de propósito.

O pedido de login é o diálogo NATIVO do próprio browser (aquela caixa
cinzenta a pedir utilizador/password), não uma página nossa -- é a forma
mais simples de implementar e a mais difícil de "saltar" por engano,
porque cobre TODOS os pedidos (páginas, imagens, vídeos, etc.), não só
as rotas que decidirmos proteger uma a uma.
"""

import hmac

from flask import current_app, request, Response


def _unauthorized():
    return Response(
        "Autenticação necessária.",
        401,
        {"WWW-Authenticate": 'Basic realm="HIM Dub Festival"'},
    )


def check_site_lock():
    """Chamado num @app.before_request -- devolve uma Response 401 (pedindo
    login) se o bloqueio estiver ligado e as credenciais não baterem certo,
    ou None para deixar o pedido seguir normalmente."""
    password = current_app.config.get("SITE_LOCK_PASSWORD", "")
    if not password:
        return None  # bloqueio desligado (comportamento normal do site)

    # O Stripe chama o webhook diretamente, servidor a servidor -- não há
    # nenhum browser ali para mostrar um diálogo de login. Tem de ficar
    # sempre acessível, senão as confirmações de pagamento (dos testes que
    # já fizeste) paravam de funcionar enquanto o site estivesse trancado.
    if request.path.startswith("/webhooks/") or request.path == "/healthz":
        return None

    username = current_app.config.get("SITE_LOCK_USERNAME", "himdub")
    auth = request.authorization
    if (
        not auth
        or not hmac.compare_digest(auth.username or "", username)
        or not hmac.compare_digest(auth.password or "", password)
    ):
        return _unauthorized()

    return None
