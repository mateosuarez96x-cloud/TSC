import configparser
import re
import sys
import requests
from bs4 import BeautifulSoup


BASE = "https://tiendasolar.com"

CARD_MAP = {
    "American Express": "1",
    "Visa": "2",
    "Mastercard": "3",
    "Dinners Club Internacional": "4",
    "Mastercard Maestro": "6",
    "Tarjeta Virtual": "8",
    "Discover Global": "11",
    "Union Pay": "12",
    "Paypal": "13",
}


def log(msg):
    print(f"[INFO] {msg}", flush=True)


def err(msg):
    print(f"[ERROR] {msg}", file=sys.stderr, flush=True)


def comprar(cfg):
    usuario = cfg["cuenta"]["usuario"]
    password = cfg["cuenta"]["password"]
    url_producto = cfg["producto"]["url"]
    cantidad = cfg["producto"].getint("cantidad")
    metodo_pago = cfg["pago"]["metodo"]
    red = cfg["pago"]["red"]
    fact = cfg["facturacion"]

    s = requests.Session()
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 Chrome/120.0.0.0"
    )

    # ---- LOGIN ----
    log("Iniciando sesion...")
    r = s.get(f"{BASE}/mi-cuenta/")
    soup = BeautifulSoup(r.text, "html.parser")
    login_form = soup.select_one("form.woocommerce-form-login")
    if not login_form:
        raise RuntimeError("No se encontro el formulario de login")

    nonce_el = login_form.find("input", {"name": "woocommerce-login-nonce"})
    if not nonce_el:
        raise RuntimeError("No se encontro el nonce de login")
    nonce = nonce_el.get("value")

    r = s.post(
        f"{BASE}/mi-cuenta/",
        data={
            "username": usuario,
            "password": password,
            "woocommerce-login-nonce": nonce,
            "_wp_http_referer": "/mi-cuenta/",
            "rememberme": "forever",
            "login": "Acceder",
        },
        allow_redirects=True,
    )

    if "Cerrar sesión" not in r.text and "logout" not in r.text.lower():
        raise RuntimeError("Login fallido — credenciales incorrectas?")
    log("Login exitoso")

    # ---- LIMPIAR CARRITO ----
    log("Limpiando carrito...")
    r = s.get(f"{BASE}/wp-json/wc/store/v1/cart/", headers={"X-Requested-With": "XMLHttpRequest"})
    if r.status_code == 200:
        cart = r.json()
        for item in cart.get("items", []):
            item_key = item.get("key")
            if item_key:
                s.delete(f"{BASE}/wp-json/wc/store/v1/cart/items/{item_key}", headers={"X-Requested-With": "XMLHttpRequest"})
                log(f"  Eliminado item {item.get('name')} x{item.get('quantity')}")
    log("Carrito limpio")

    # ---- PRODUCTO ----
    log(f"Obteniendo producto: {url_producto}")
    r = s.get(url_producto)
    soup = BeautifulSoup(r.text, "html.parser")

    product_name_el = soup.find("h1", class_="product_title")
    product_name = product_name_el.get_text(strip=True) if product_name_el else "Producto"
    log(f"Producto: {product_name}")

    pid_el = soup.find("button", {"name": "add-to-cart"})
    if not pid_el:
        pid_el = soup.find("input", {"name": "add-to-cart"})
    if not pid_el:
        raise RuntimeError("No se encontro el product_id en la pagina")
    product_id = pid_el.get("value")
    log(f"Product ID: {product_id}")

    # Ubicaciones
    loc_sel = soup.find("select", {"name": "wcmlim_change_lc_to"})
    if not loc_sel:
        raise RuntimeError("No se encontro el selector de ubicaciones en la pagina")

    loc_options = []
    for opt in loc_sel.find_all("option"):
        val = opt.get("value")
        if val and val != "-1":
            name = opt.get_text(strip=True)
            term = opt.get("data-lc-term")
            loc_options.append({"value": val, "name": name, "term": term})

    if not loc_options:
        raise RuntimeError("No hay ubicaciones disponibles")

    log(f"Ubicaciones encontradas: {[l['name'] for l in loc_options]}")

    loc_nonce_el = soup.find("input", {"id": "wcmlim_change_lc_nonce"})
    loc_nonce = loc_nonce_el.get("value") if loc_nonce_el else ""

    # ---- PROBAR DISPONIBILIDAD ----
    chosen_location = None
    for loc in loc_options:
        log(f"Probando ubicacion: {loc['name']}...")
        s.post(
            f"{BASE}/wp-admin/admin-ajax.php",
            data={
                "action": "wcmlim_location_change",
                "wcmlim_change_lc_to": loc["value"],
                "wcmlim_change_lc_nonce": loc_nonce,
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        r = s.get(url_producto)
        soup = BeautifulSoup(r.text, "html.parser")

        stock_el = soup.find("p", class_="stock")
        if stock_el and "in-stock" in stock_el.get("class", []):
            match = re.search(r"(\d+)", stock_el.get_text())
            stock_qty = int(match.group(1)) if match else 0
            log(f"  Stock: {stock_qty}")
            if stock_qty >= cantidad:
                chosen_location = loc
                break

    if not chosen_location:
        raise RuntimeError(
            f"El producto no tiene stock suficiente en ninguna ubicacion "
            f"(cantidad solicitada: {cantidad})"
        )

    log(f"Ubicacion seleccionada: {chosen_location['name']} (term={chosen_location['term']})")

    # ---- AÑADIR AL CARRITO ----
    log(f"Añadiendo {cantidad} unidad(es) al carrito...")
    s.post(
        f"{BASE}/wp-admin/admin-ajax.php",
        data={
            "action": "wcmlim_location_change",
            "wcmlim_change_lc_to": chosen_location["value"],
            "wcmlim_change_lc_nonce": loc_nonce,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    s.post(
        url_producto,
        data={
            "add-to-cart": product_id,
            "quantity": cantidad,
            "select_location": chosen_location["value"],
        },
        allow_redirects=True,
    )
    r = s.get(f"{BASE}/carrito/")
    if "cart-empty" in r.text:
        raise RuntimeError("No se pudo añadir el producto al carrito")
    log("Producto añadido al carrito correctamente")

    solo_montar = cfg.getboolean("comportamiento", "solo_montar", fallback=False)
    if solo_montar:
        mensaje = (
            f"Producto añadido al carrito:\n"
            f"  Producto: {product_name}\n"
            f"  Cantidad: {cantidad}\n"
            f"  Ubicación: {chosen_location['name']}"
        )
        log("Modo solo_montar activo — deteniendo antes del checkout")
        return {"tipo": "carrito", "mensaje": mensaje}

    # ---- CHECKOUT ----
    log("Accediendo al checkout...")
    r = s.get(f"{BASE}/finalizar-compra/", allow_redirects=True)
    if "finalizar-compra" not in r.url.lower():
        raise RuntimeError(f"Redirigido fuera del checkout: {r.url}")
    soup = BeautifulSoup(r.text, "html.parser")

    checkout_nonce_el = soup.find("input", {"name": "woocommerce-process-checkout-nonce"})
    checkout_nonce = checkout_nonce_el.get("value") if checkout_nonce_el else ""

    shipping_method_el = soup.find("input", {"name": "shipping_method[0]"})
    shipping_method = shipping_method_el.get("value") if shipping_method_el else "local_pickup:2"

    # Validar / autodescubrir el campo bipay
    bipay_box = soup.find("div", class_="payment_method_bipay")
    if not bipay_box:
        raise RuntimeError("No se encontro el contenedor payment_method_bipay en la pagina")
    select_el = bipay_box.find("select")
    if not select_el:
        raise RuntimeError("No se encontro el select dentro de payment_method_bipay")
    campo_red = select_el.get("name")
    log(f"Campo bipay autodescubierto: {campo_red}")
    red_valor = None
    for opt in select_el.find_all("option"):
        if opt.get_text(strip=True).lower() == red.lower():
            red_valor = opt.get("value")
            break
    if not red_valor and red in CARD_MAP:
        red_valor = CARD_MAP[red]
    if not red_valor:
        raise RuntimeError(f"No se pudo mapear la red '{red}' a un valor del select")
    log(f"Red '{red}' -> valor {red_valor}")

    # Datos de facturacion y destinatario
    dest = cfg["destinatario"] if "destinatario" in cfg else {}

    checkout_data = {
        "payment_method": metodo_pago,
        campo_red: red_valor,
        "billing_first_name": fact["first_name"],
        "billing_last_name": fact["last_name"],
        "billing_email": fact.get("email", fact["email"]),
        "billing_phone": fact["phone"],
        "billing_country": fact["country"],
        "billing_address_1": fact["address_1"],
        "billing_address_2": fact.get("address_2", ""),
        "billing_city": fact["city"],
        "billing_state": fact["state"],
        "billing_postcode": fact["postcode"],
        "shipping_method[0]": shipping_method,
        "nombre_destinatario": dest.get("nombre", fact["first_name"]),
        "apellidos_destinatario": dest.get("apellidos", fact["last_name"]),
        "carnet_identidad_destinatario": dest.get("carnet_identidad", ""),
        "telefono_destinatario": dest.get("telefono", fact["phone"]),
        "celular_destinatario": dest.get("celular", fact["phone"]),
        "terms": "1",
        "terms-field": "1",
        "woocommerce-process-checkout-nonce": checkout_nonce,
        "_wp_http_referer": f"/finalizar-compra/",
    }

    log("Procesando checkout...")
    r = s.post(
        f"{BASE}/?wc-ajax=checkout",
        data=checkout_data,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    try:
        resp = r.json()
    except ValueError:
        raise RuntimeError(f"Respuesta del checkout no es JSON valido: {r.text[:500]}")

    if resp.get("result") != "success":
        raise RuntimeError(f"Checkout fallido: {resp.get('messages', str(resp)[:500])}")

    order_id = resp.get("order_id", "N/A")
    redirect_url = resp.get("redirect", "")

    log(f"Pedido creado exitosamente! Orden ID: {order_id}")
    if redirect_url:
        log(f"URL DE PAGO: {redirect_url}")
    else:
        log(f"No se recibio URL de redireccion. Respuesta: {str(resp)[:500]}")

    return {"tipo": "pago", "url": redirect_url}


def main():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    try:
        res = comprar(cfg)
        if res["tipo"] == "pago" and res.get("url"):
            with open("url.txt", "w") as f:
                f.write(res["url"])
            print(f"\nURL guardada en url.txt")
        elif res["tipo"] == "carrito":
            print(f"\n{res['mensaje']}")
        return True
    except RuntimeError as e:
        err(str(e))
        return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
