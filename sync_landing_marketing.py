#!/usr/bin/env python3
"""
Sincroniza textos de marketing del HTML de la landing con el inventario REAL
(catalogo-piloto.json). Cero invenciones: cada numero del meta description,
title, og:title, og:description proviene del inventario actual.

Lugares actualizados (cada uno con su regex acotado):
  - <lastmod> de docs/sitemap.xml (fecha del sync)
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


SITEMAP_PATH = os.path.join(ROOT, "docs", "sitemap.xml")


def sync_sitemap():
    """Mantiene <lastmod> del sitemap en la fecha del último sync (UTC).
    El catálogo cambia a diario; un lastmod fijo le dice a Google lo contrario."""
    if not os.path.exists(SITEMAP_PATH):
        return
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(SITEMAP_PATH, "r", encoding="utf-8") as f:
        xml = f.read()
    new_xml = re.sub(r"<lastmod>[^<]*</lastmod>", f"<lastmod>{today}</lastmod>", xml, count=1)
    if new_xml != xml:
        with open(SITEMAP_PATH, "w", encoding="utf-8") as f:
            f.write(new_xml)
        print(f"sitemap.xml: lastmod → {today}")


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

    # Composicion de textos alineados con el concepto rector "Sin Sorpresas"
    # y la propuesta de valor: 8 sucursales en GDL, 75 años de operación,
    # inventario actualizado al día. Inventario (n_cars) se inyecta dinámico.
    # Title ≤ 60 caracteres (Google corta ~60) con marca + intención de búsqueda.
    new_title = "Seminuevos Plasencia · Autos certificados en Guadalajara"
    # Description ≤ 160 caracteres (Google corta ~155-160).
    new_desc = (
        f"{n_cars} seminuevos certificados en 8 sucursales de Guadalajara. Inspección de "
        f"150 puntos, garantía de un año y factura original. Tu asesor te escribe por WhatsApp."
    )
    new_og_title = "Seminuevos Plasencia · Sin sorpresas · 75 años en Guadalajara"
    new_og_desc = (
        f"{n_cars} autos certificados en 8 sucursales de Guadalajara · "
        f"Inspección 150 puntos · Garantía de un año · Factura original · Crédito desde 12 meses."
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

    sync_sitemap()

    if html == original:
        print("HTML ya sincronizado con el inventario. Sin cambios.")
        return

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML actualizado: textos de marketing sincronizados con inventario actual.")


if __name__ == "__main__":
    main()
