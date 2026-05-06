#!/usr/bin/env python3
"""
Export catalogo-piloto.json desde D1 (tabla inventario_seminuevos).

Cero invenciones: cada campo del JSON proviene 1:1 del feed XML de
Maxipublica via D1. Si un dato no esta en el feed, no se muestra (la
landing oculta el chip).

Lee filas con piloto_otero=1 AND activo=1 desde D1 via REST API.
Genera JSON en formato consumible por la landing (fetch).

Variables de entorno:
  - CLOUDFLARE_API_TOKEN  (scope D1:Read minimo)
  - CF_ACCOUNT_ID         (default: 3a73c6035cae8b2bfab359a16ec44fca)
  - D1_DATABASE_ID        (default: ad3e7ad8-55e0-4436-941a-6e299638af8e)
"""

import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT, ".env")
OUTPUT_PATH = os.path.join(ROOT, "docs", "catalogo-piloto.json")

# Mapping de agencia_id → nombre legible para la landing.
# Cuando alguna pill matchea, se usa este nombre. Cuando NO matchea, se usa
# el AGENCIA_NOMBRE que viene en el feed (sin invento).
SUCURSAL_OVERRIDES = {
    3852: "López Mateos",
    4199: "Mazda Plasencia",
    3886: "Mazda Galerías",
    3736: "Mazda Américas",
    3888: "Mazda Acueducto",
    3737: "Mazda González Gallo",
    3885: "Mazda Santa Anita",
}


def load_env():
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def d1_query(sql, params=None):
    env = load_env()
    token = env.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
    account = env.get("CF_ACCOUNT_ID") or os.environ.get("CF_ACCOUNT_ID") or "3a73c6035cae8b2bfab359a16ec44fca"
    db = env.get("D1_DATABASE_ID") or os.environ.get("D1_DATABASE_ID") or "ad3e7ad8-55e0-4436-941a-6e299638af8e"
    if not token:
        raise SystemExit("CLOUDFLARE_API_TOKEN no esta seteado")

    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{db}/query"
    body = json.dumps({"sql": sql, "params": params or []}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())

    if not data.get("success"):
        raise RuntimeError(f"D1 error: {data.get('errors')}")
    return data["result"][0]["results"]


def get_loc(row, extra):
    """Sucursal legible. Prioridad:
       1. Override editorial por agencia_id (formato bonito con acentos).
       2. AGENCIA_NOMBRE del feed (cae aqui si la agencia es nueva).
       3. Cadena vacia si nada.
    """
    aid = row.get("agencia_id")
    if aid in SUCURSAL_OVERRIDES:
        return SUCURSAL_OVERRIDES[aid]
    return row.get("agencia_nombre") or extra.get("AGENCIA_NOMBRE", "") or ""


def transform(row):
    """Mapea row de D1 al shape esperado por la landing.

    Cero invenciones: cada campo viene del feed XML. Los que no estan en
    columnas dedicadas vienen del data_json (que el worker pobla con todo
    lo no-dedicado del payload del scraper).
    """
    extra = {}
    if row.get("data_json"):
        try:
            extra = json.loads(row["data_json"])
        except Exception:
            extra = {}

    # Imagenes: prefiero data_json (lista en orden), fallback al thumbnail.
    imgs_raw = extra.get("IMAGENES_URLS")
    gallery = []
    if isinstance(imgs_raw, list):
        gallery = [str(u).strip() for u in imgs_raw if u]
    elif isinstance(imgs_raw, str):
        gallery = [u.strip() for u in imgs_raw.strip().split("\n") if u.strip()]
    img = gallery[0] if gallery else (row.get("thumbnail") or "")

    # Badge: solo "Destacado" si la columna manual esta marcada. Cero badges
    # automaticos por bono — cuando regrese oferta con detalle por unidad
    # restaurar logica historica.
    destacado = bool(row.get("destacado"))
    badge = "Destacado" if destacado else ""

    # Equipamiento (parseado por el parser_xml_maxipublica del <description>).
    # Si no se pudo parsear, queda dict vacio — la UI omite la seccion.
    equipamiento = extra.get("EQUIPAMIENTO") or {}
    if not isinstance(equipamiento, dict):
        equipamiento = {}

    return {
        # Identidad y precio
        "id": row.get("id_auto", 0),
        "name": f"{row.get('marca','') or ''} {row.get('modelo','') or ''}".strip(),
        "year": int(row.get("anio") or 0),
        "price": int(row.get("precio") or 0),
        "brand": row.get("marca") or "",

        # Specs principales (chips de cards y modal)
        "type": (row.get("segmento") or "").lower() or "otro",
        "trans": row.get("transmision") or "",
        "fuel": row.get("combustible") or "",
        "color": row.get("color_ext") or "",
        "km": int(row.get("odometro_km") or 0),

        # Specs ampliados (modal — chips secundarios). None / 0 / "" ⇒ chip oculto.
        "color_int": extra.get("COLOR_INT") or "",
        "puertas": extra.get("PUERTAS") or 0,
        "pasajeros": extra.get("PASAJEROS") or 0,
        "velocidades": extra.get("VELOCIDADES") or 0,
        "cilindros": extra.get("CILINDROS") or 0,
        "hp": extra.get("POTENCIA_HP") or 0,
        "traccion": row.get("traccion") or extra.get("TRACCION") or "",
        "tanque_l": extra.get("TANQUE_L") or 0,
        "rines": extra.get("RINES_PULGADAS") or 0,
        "consumo_combinado": extra.get("CONSUMO_COMBINADO") or 0,
        "consumo_ciudad": extra.get("CONSUMO_CIUDAD") or 0,
        "consumo_carretera": extra.get("CONSUMO_CARRETERA") or 0,

        # Equipamiento por categoria (Audio/Confort/Seguridad/Gadgets/etc.)
        # Solo se incluye si la seccion tiene items.
        "equipamiento": equipamiento,

        # Sucursal y agencia
        "loc": get_loc(row, extra),
        "agencia_id": row.get("agencia_id"),

        # Imagenes
        "img": img,
        "gallery": gallery,
        "photos": int(row.get("total_imagenes") or len(gallery)),

        # Badge editorial (no se calcula automatico)
        "badge": badge,
    }


def main():
    print("Leyendo D1 inventario_seminuevos (piloto_otero=1, activo=1)...")
    rows = d1_query(
        "SELECT * FROM inventario_seminuevos WHERE piloto_otero = 1 AND activo = 1"
    )
    print(f"  {len(rows)} registros activos del piloto")

    vehicles = [transform(r) for r in rows]
    vehicles.sort(key=lambda x: x["price"], reverse=True)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(vehicles, f, ensure_ascii=False, indent=None, separators=(",", ":"))

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"  Exportado: {OUTPUT_PATH} ({size_kb:.1f} KB)")

    if vehicles:
        prices = [v["price"] for v in vehicles if v["price"]]
        brands = set(v["name"].split()[0] for v in vehicles if v["name"])
        with_eq = sum(1 for v in vehicles if v["equipamiento"])
        print(f"  Marcas: {len(brands)} | Precio: ${min(prices):,}-${max(prices):,} | Promedio: ${sum(prices)//len(prices):,}")
        print(f"  Con equipamiento parseado: {with_eq}/{len(vehicles)}")


if __name__ == "__main__":
    main()
