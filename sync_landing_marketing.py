#!/usr/bin/env python3
"""
Sincroniza textos de marketing del HTML de la landing con el inventario REAL
(catalogo-piloto.json). Cero invenciones: cada numero del meta description,
title, og:title, og:description proviene del inventario actual.

Lugares actualizados (cada uno con su regex acotado):
  - <title>
  - <meta name="description" ...>
  - <meta property="og:title" ...>
  - <meta property="og:description" ...>

Idempotente: si el HTML ya esta sincronizado con el JSON, no escribe nada
(asi el workflow no genera commits ruidosos).

Se invoca despues de export_catalogo_piloto.py en el workflow horario.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(ROOT, "docs", "catalogo-piloto.json")
HTML_PATH = os.path.join(ROOT, "docs", "index.html")


def fmt_price_compact(n):
    """179000 -> '$179K'  |  1500000 -> '$1.5M'"""
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"${v:.1f}M".replace(".0M", "M")
    return f"${round(n / 1000)}K"


def main():
    if not os.path.exists(JSON_PATH):
        print(f"ERROR: {JSON_PATH} no existe (corre primero export_catalogo_piloto.py)", file=sys.stderr)
        sys.exit(1)

    with open(JSON_PATH) as f:
        vehicles = json.load(f)

    if not vehicles:
        print("WARN: catalogo vacio. No se actualizan textos de marketing.")
        return

    n_cars = len(vehicles)
    brands = sorted({v.get("brand") for v in vehicles if v.get("brand")})
    n_brands = len(brands)
    valid_prices = [v["price"] for v in vehicles if isinstance(v.get("price"), (int, float)) and v["price"] > 0]
    min_price = min(valid_prices) if valid_prices else 0
    min_price_compact = fmt_price_compact(min_price) if min_price else ""

    print(f"Inventario actual: {n_cars} autos · {n_brands} marcas · desde {min_price_compact}")

    # Composicion de textos. Plantillas fijas con valores reales del inventario.
    new_title = f"Seminuevos Plasencia Guadalajara | Autos certificados desde {min_price_compact}"
    new_desc = (
        f"Tu próximo seminuevo verificado en Guadalajara. Inspección de 150 puntos, "
        f"1 año de garantía y factura original. {n_brands} marcas desde {min_price_compact}. "
        f"Crédito desde 12 meses."
    )
    new_og_title = f"Seminuevos Plasencia — Autos certificados desde {min_price_compact} | Guadalajara"
    new_og_desc = (
        f"Tu próximo seminuevo verificado · Inspección 150 puntos · 1 año de garantía · "
        f"factura original · {n_brands} marcas desde {min_price_compact} · crédito desde 12 meses."
    )

    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    original = html

    # Reemplazos con regex acotado (solo el primer match — son tags unicos).
    html = re.sub(
        r"(<title>)[^<]*(</title>)",
        lambda m: m.group(1) + new_title + m.group(2),
        html, count=1
    )
    html = re.sub(
        r'(<meta\s+name="description"\s+content=")[^"]*(")',
        lambda m: m.group(1) + new_desc + m.group(2),
        html, count=1
    )
    html = re.sub(
        r'(<meta\s+property="og:title"\s+content=")[^"]*(")',
        lambda m: m.group(1) + new_og_title + m.group(2),
        html, count=1
    )
    html = re.sub(
        r'(<meta\s+property="og:description"\s+content=")[^"]*(")',
        lambda m: m.group(1) + new_og_desc + m.group(2),
        html, count=1
    )

    if html == original:
        print("HTML ya sincronizado con el inventario. Sin cambios.")
        return

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML actualizado: textos de marketing sincronizados con inventario actual.")


if __name__ == "__main__":
    main()
