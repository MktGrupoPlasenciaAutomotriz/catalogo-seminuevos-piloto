#!/usr/bin/env python3
"""
Export catalogo-piloto.json desde Airtable (view Piloto Otero).
Genera JSON en formato consumible por el prototipo HTML (fetch).

Usa REST API directo con PAT (misma logica que el upserter).
Lee .env para credenciales.
"""

import json
import os
import urllib.request
import urllib.parse

# --- Config ---
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "airtable_config.json")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "docs", "catalogo-piloto.json")

def load_env():
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    # Fallback to os.environ (GitHub Actions)
    return {
        "pat": env.get("AIRTABLE_PAT", os.environ.get("AIRTABLE_PAT", "")),
        "base_id": env.get("AIRTABLE_BASE_ID", os.environ.get("AIRTABLE_BASE_ID", "")),
        "table_id": env.get("AIRTABLE_TABLE_ID", os.environ.get("AIRTABLE_TABLE_ID", "")),
    }

def fetch_piloto_otero(pat, base_id, table_id):
    """Fetch all records from Piloto Otero view via Airtable REST API."""
    records = []
    offset = None
    url_base = f"https://api.airtable.com/v0/{base_id}/{table_id}"

    while True:
        params = {
            "view": "Piloto Otero",
            "pageSize": "100",
        }
        if offset:
            params["offset"] = offset

        url = url_base + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
        })

        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())

        for rec in data.get("records", []):
            records.append(rec["fields"])

        offset = data.get("offset")
        if not offset:
            break

    return records

def segment_to_type(seg):
    seg = (seg or "").lower()
    if "suv" in seg or "crossover" in seg: return "suv"
    if "pick" in seg or "camioneta" in seg: return "pickup"
    if "hatchback" in seg or "hatch" in seg: return "hatchback"
    if "van" in seg or "minivan" in seg: return "van"
    if "coupé" in seg or "coupe" in seg or "deportivo" in seg: return "coupe"
    return "sedan"

def get_engine(fields):
    cyl = fields.get("CILINDROS")
    hp = fields.get("POTENCIA_HP")
    parts = []
    if cyl: parts.append(f"{int(cyl)} cil")
    if hp: parts.append(f"{int(hp)} hp")
    return ", ".join(parts) if parts else ""

def get_img(fields):
    imgs = fields.get("IMAGENES_URLS")
    if imgs and isinstance(imgs, str):
        # Airtable returns multilineText as string with newlines
        first = imgs.strip().split("\n")[0].strip()
        if first: return first
    thumb = fields.get("THUMBNAIL", "")
    return thumb or ""

def get_loc(fields):
    aid = fields.get("AGENCIA_ID")
    if aid == 4199:
        return "Mazda Plasencia"
    return "Lopez Mateos"

# --- Bonos de la oferta comercial Abril 2026 (Ford Plasencia) ---
# Fuente: artes de Meta Ads entregados por Jose Reyes
# Regla: autos del lote Ford (agencia 3852) tienen bono segun precio
# Hyundai (agencia 4199) no tiene bonos
BONOS_FORD = {
    # precio_range: bono
    # $15,000 para: Expedition, Suburban, Gladiator, Expedition 2021
    # $10,000 para: Maverick, Koleos, CX9, Bronco, Explorer
    # $7,500 para: Silverado, Ranger
    # $5,000 para: el resto
}

def get_bono(fields):
    """Asigna bono segun oferta comercial abril 2026."""
    aid = fields.get("AGENCIA_ID")
    if aid != 3852:  # Solo lote Ford/multimarca tiene bonos
        return 0
    price = fields.get("PRECIO", 0) or 0
    marca = (fields.get("MARCA", "") or "").lower()
    modelo = (fields.get("MODELO", "") or "").lower()
    # Bonos altos para vehiculos premium
    if price >= 750000:
        return 15000
    if price >= 450000:
        return 10000
    if price >= 350000:
        return 7500
    return 5000

def transform(fields):
    destacado = fields.get("DESTACADO", False)
    bono = get_bono(fields)
    if bono > 0:
        badge = f"Bono ${bono:,}"
    elif destacado:
        badge = "Destacado"
    else:
        badge = ""

    return {
        "id": fields.get("ID_AUTO", 0),
        "name": f"{fields.get('MARCA', '')} {fields.get('MODELO', '')}".strip(),
        "year": fields.get("ANIO", 0) or 0,
        "version": fields.get("TRIM", "") or "",
        "type": segment_to_type(fields.get("SEGMENTO", "")),
        "km": int(fields.get("ODOMETRO_KM", 0) or 0),
        "trans": fields.get("TRANSMISION", "") or "",
        "fuel": fields.get("COMBUSTIBLE", "") or "",
        "engine": get_engine(fields),
        "color": fields.get("COLOR_EXT", "") or "",
        "price": int(fields.get("PRECIO", 0) or 0),
        "img": get_img(fields),
        "badge": badge,
        "photos": int(fields.get("TOTAL_IMAGENES", 0) or 0),
        "loc": get_loc(fields),
    }

def main():
    env = load_env()
    if not env["pat"]:
        print("ERROR: AIRTABLE_PAT no encontrado en .env ni en env vars")
        return

    print("Leyendo Airtable view 'Piloto Otero'...")
    records = fetch_piloto_otero(env["pat"], env["base_id"], env["table_id"])
    print(f"  {len(records)} registros")

    vehicles = [transform(r) for r in records if r.get("ACTIVO", False)]
    vehicles.sort(key=lambda x: x["price"], reverse=True)
    print(f"  {len(vehicles)} activos (ordenados por precio desc)")

    # Ensure output dir exists
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(vehicles, f, ensure_ascii=False, indent=None, separators=(",", ":"))

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"  Exportado: {OUTPUT_PATH} ({size_kb:.1f} KB)")

    # Stats
    if vehicles:
        prices = [v["price"] for v in vehicles if v["price"]]
        brands = set(v["name"].split()[0] for v in vehicles if v["name"])
        print(f"  Marcas: {len(brands)} | Precio: ${min(prices):,}-${max(prices):,} | Promedio: ${sum(prices)//len(prices):,}")

if __name__ == "__main__":
    main()
