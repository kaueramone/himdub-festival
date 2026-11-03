"""Smoke test rápido: percorre as páginas principais e o fluxo de carrinho/checkout."""
from app import create_app

app = create_app()
app.config["TESTING"] = True
# O bloqueio do site inteiro por password (ver site_lock.py) e uma coisa de
# deploy, nao faz sentido interferir com este teste -- desliga-se aqui.
app.config["SITE_LOCK_PASSWORD"] = ""
# O mesmo para a pagina "brevemente" (ver coming_soon.py) -- tambem e um
# toggle de deploy, nao faz sentido bloquear todas as paginas durante este
# teste.
app.config["COMING_SOON_MODE"] = False

client = app.test_client()

GET_ROUTES = [
    "/", "/the-festival", "/line-up", "/participate", "/ambassadors", "/the-map",
    "/tickets", "/the-experience", "/merchandise",
    "/infos/faq", "/infos/sustainability", "/infos/partners", "/infos/contact",
    "/cart", "/product/bilhete-geral", "/product/hoodie-music-is-the-most-high",
]

failures = []

# /glamping-village e o nome antigo da rota (ver blueprints/shop.py) -- deve
# continuar a redirecionar (301) para /the-experience, nunca dar 404.
r = client.get("/glamping-village")
ok = r.status_code == 301 and r.location.endswith("/the-experience")
print(f"{'OK' if ok else 'FAIL':12} /glamping-village -> redirect")
if not ok:
    failures.append("/glamping-village (redirect)")

for route in GET_ROUTES:
    resp = client.get(route)
    status = "OK" if resp.status_code == 200 else f"FAIL ({resp.status_code})"
    if resp.status_code != 200:
        failures.append(route)
    print(f"{status:12} {route}")

# fluxo de carrinho -> checkout demo
r = client.post("/cart/add", data={"product_id": 1, "quantity": 2})
print("cart add ->", r.status_code, r.location if r.status_code in (301,302) else "")

r = client.post("/cart/add", data={"product_id": 9, "variant": "M", "quantity": 1})
print("cart add variant ->", r.status_code)

r = client.get("/cart")
print("cart page ->", r.status_code, "OK" if b"Resumo da encomenda" in r.data else "MISSING CONTENT")

r = client.get("/checkout")
print("checkout GET ->", r.status_code)

r = client.post("/checkout", data={
    "name": "Teste", "email": "teste@example.com", "country": "Portugal",
    # product_id 1 (bilhete-geral) foi adicionado com quantity=2 acima --
    # nome de bilhete agora obrigatório para cada unidade (ver
    # _needs_ticket_names em blueprints/shop.py). product_id 9
    # (embaixador-burkina-faso, variante M) também é categoria "ticket",
    # por isso também precisa do nome do seu único bilhete.
    "ticket_name__1__0": "Fulano Teste", "ticket_name__1__1": "Beltrano Teste",
    "ticket_name__9:M__0": "Sicrano Teste",
}, follow_redirects=True)
print("checkout POST ->", r.status_code, "OK" if b"foi confirmada" in r.data else "MISSING CONTENT")

# validação do checkout (nome/email em falta ou inválido não deve criar encomenda)
# (a encomenda anterior já esvaziou o carrinho, por isso adicionamos outro item primeiro)
client.post("/cart/add", data={"product_id": 1, "quantity": 1})
r = client.post("/checkout", data={"name": "", "email": "nao-e-email"})
ok = b"Indica o teu nome" in r.data and b"Indica um email" in r.data
print("checkout validation ->", "OK" if ok else "FAILED (mensagens de erro em falta)")
if not ok:
    failures.append("/checkout (validação)")

# validação específica do nome por bilhete: preenche tudo menos o nome do
# bilhete e confirma que a encomenda não é criada sem ele.
r = client.post("/checkout", data={"name": "Teste", "email": "teste@example.com", "country": "Portugal"})
ok = b"Indica o nome de cada bilhete" in r.data
print("checkout validation (nome do bilhete) ->", "OK" if ok else "FAILED (mensagem em falta)")
if not ok:
    failures.append("/checkout (validação nome do bilhete)")

# área de administração: bloqueada sem sessão, acessível com a password certa
r = client.get("/admin/orders", follow_redirects=True)
ok = b"Entrar como administrador" in r.data
print("admin sem login ->", "OK (bloqueado)" if ok else "FAILED (devia bloquear)")
if not ok:
    failures.append("/admin/orders (proteção)")

# bilhetes com QR + check-in: comprar um bilhete deve gerar um Ticket;
# merchandise/glamping não geram bilhete.
# (usa um client novo, com carrinho/sessão limpos, para não herdar itens
# deixados no carrinho pelos testes de checkout anteriores)
from models import Order, Ticket  # noqa: E402

client2 = app.test_client()
client2.post("/cart/add", data={"product_id": 1, "quantity": 1})  # bilhete
r = client2.post("/checkout", data={
    "name": "Fumo Teste", "email": "fumo@example.com", "country": "Spain",
    "ticket_name__1__0": "Fumo Teste",
}, follow_redirects=True)
with app.app_context():
    order = Order.query.filter_by(email="fumo@example.com").order_by(Order.id.desc()).first()
    ok = order is not None and len(order.tickets) == 1
    print("bilhete gerado no checkout ->", "OK" if ok else "FAILED (devia gerar 1 Ticket)")
    if not ok:
        failures.append("geração de bilhetes/QR")
    ticket_code = order.tickets[0].code if ok else None
    ok2 = ok and order.tickets[0].holder_name == "Fumo Teste"
    print("nome do bilhete gravado ->", "OK" if ok2 else "FAILED (holder_name devia vir preenchido)")
    if not ok2:
        failures.append("nome por bilhete (holder_name)")

if ticket_code:
    r = client2.get(f"/checkin/{ticket_code}", follow_redirects=True)
    ok = b"Entrar como administrador" in r.data
    print("checkin sem login ->", "OK (bloqueado)" if ok else "FAILED (devia bloquear)")
    if not ok:
        failures.append("/checkin (proteção)")

    client2.post("/admin/login", data={"password": app.config.get("ADMIN_PASSWORD", "")})
    r = client2.post(f"/checkin/{ticket_code}", data={"wristband_code": "TESTE-001"}, follow_redirects=True)
    ok = b"TESTE-001" in r.data
    print("checkin associa pulseira ->", "OK" if ok else "FAILED")
    if not ok:
        failures.append("/checkin (associação de pulseira)")

print()
print("FAILURES:", failures if failures else "none")
