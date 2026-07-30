import configparser
import os
import sys
import traceback

import requests
from flask import Flask, jsonify

from comprar import comprar, log, err

app = Flask(__name__)


def enviar_telegram(mensaje):
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    if "telegram" not in cfg:
        log("Seccion [telegram] no encontrada en config.ini")
        return False
    token = cfg["telegram"]["bot_token"]
    chat_id = cfg["telegram"]["chat_id"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={
        "chat_id": chat_id,
        "text": mensaje,
        "disable_web_page_preview": True,
    })
    if r.status_code == 200:
        log("Mensaje enviado a Telegram")
        return True
    else:
        err(f"Error al enviar a Telegram: {r.status_code} {r.text[:200]}")
        return False


@app.route("/comprar", methods=["GET"])
def comprar_endpoint():
    log("=" * 60)
    log("Iniciando compra automatica...")
    log("=" * 60)

    cfg = configparser.ConfigParser()
    cfg.read("config.ini")

    try:
        url_pago = comprar(cfg)
    except RuntimeError as e:
        erro = str(e)
        err(erro)
        enviar_telegram(f"[ERROR] Compra fallida: {erro}")
        return jsonify({"ok": False, "error": erro}), 500
    except Exception as e:
        tb = traceback.format_exc()
        err(f"Error inesperado: {e}\n{tb}")
        enviar_telegram(f"[ERROR] Error inesperado: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

    if not url_pago:
        msg = "Compra completada pero no se recibio URL de pago"
        err(msg)
        enviar_telegram(msg)
        return jsonify({"ok": False, "error": msg}), 500

    msg = (
        f"Pedido creado exitosamente!\n\n"
        f"URL de pago: {url_pago}"
    )
    log(msg)
    enviar_telegram(msg)

    return jsonify({"ok": True, "url": url_pago})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
