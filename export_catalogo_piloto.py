#!/usr/bin/env python3
"""
Export catalogo-piloto.json desde D1 (tabla inventario_seminuevos).

Reemplaza la version Airtable. Cero dependencia de Airtable.

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
    results = data["result"][0]["results"]
    return results


def segment_to_type(seg):
    seg = (seg or "").lower()
    if "suv" in seg or "crossover" in seg: return "suv"
    if "pick" in seg or "camioneta" in seg: return "pickup"
    if "hatchback" in seg or "hatch" in seg: return "hatchback"
    if "van" in seg or "minivan" in seg: return "van"
    if "coupé" in seg or "coupe" in seg or "deportivo" in seg: return "coupe"
    return "sedan"


def get_engine(cilindros, hp):
    parts = []
    if cilindros: parts.append(f"{int(cilindros)} cil")
    if hp: parts.append(f"{int(hp)} hp")
    return ", ".join(parts) if parts else ""


def get_loc(aid):
    return {
        3852: "Lopez Mateos",
        4199: "Mazda Plasencia",
        3886: "Mazda Galerias",
        3736: "Mazda Americas",
        3888: "Mazda Acueducto",
        3737: "Mazda Gonzalez Gallo",
        3885: "Mazda Santa Anita",
    }.get(aid, "Lopez Mateos")


# --- Bonos de la oferta comercial Abril 2026 (Seminuevos Plasencia) ---
# Fuente: artes de Meta Ads entregados por Jose Reyes (Gerente Ford+Semi).
# Regla: TODOS los autos en agencias del piloto reciben bono segun precio.
# Fase 1 (abril 2026): Lote Otero (3852 Seminuevos Plasencia Lopez Mateos + 4199 Mazda Plasencia).
# Fase 2 (abril 2026): extension Mazda GDL metro (3886, 3736, 3888, 3737, 3885).
# Confirmado por Chucho Porras (Dir Mkt): "aplica la misma oferta comercial" para las 5 Mazda nuevas.
PILOTO_AGENCIAS_CON_BONO = {3852, 4199, 3886, 3736, 3888, 3737, 3885}


def get_bono(row):
    """Asigna bono segun oferta comercial vigente.

    Oferta abril 2026 (cerrada el 30-abr): escala $5K-$15K segun precio.
    Oferta mayo 2026 (vigencia 22-abr al 10-may): bono $20,000 sin detalle
    por unidad (asesor confirma 1:1 con cliente). NO se calcula badge
    automatico durante esta vigencia.

    Cuando vuelva a haber oferta con badge por unidad, restaurar la
    escala (ver historico en commit 5f3f155 o anterior)."""
    return 0


def transform(row):
    """Mapea row de D1 al shape esperado por la landing."""
    # data_json contiene los campos no-dedicados del scraper
    extra = {}
    if row.get("data_json"):
        try:
            extra = json.loads(row["data_json"])
        except Exception:
            extra = {}

    # IMAGENES_URLS viene en data_json (no es columna dedicada)
    imgs_raw = extra.get("IMAGENES_URLS")
    gallery = []
    if isinstance(imgs_raw, list):
        gallery = [str(u).strip() for u in imgs_raw if u]
    elif isinstance(imgs_raw, str):
        gallery = [u.strip() for u in imgs_raw.strip().split("\n") if u.strip()]

    img = gallery[0] if gallery else (row.get("thumbnail") or "")

    destacado = bool(row.get("destacado"))
    bono = get_bono(row)
    if bono > 0:
        badge = f"Bono ${bono:,}"
    elif destacado:
        badge = "Destacado"
    else:
        badge = ""

    return {
        "id": row.get("id_auto", 0),
        "name": f"{row.get('marca','') or ''} {row.get('modelo','') or ''}".strip(),
        "year": int(row.get("anio") or 0),
        "version": row.get("trim") or "",
        "type": segment_to_type(row.get("segmento")),
        "km": int(row.get("odometro_km") or 0),
        "trans": row.get("transmision") or "",
        "fuel": row.get("combustible") or "",
        "engine": get_engine(row.get("cilindros"), row.get("potencia_hp")),
        "color": row.get("color_ext") or "",
        "price": int(row.get("precio") or 0),
        "img": img,
        "badge": badge,
        "photos": int(row.get("total_imagenes") or 0),
        "loc": get_loc(row.get("agencia_id")),
        "brand": row.get("marca") or "",
        "gallery": gallery,
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
        print(f"  Marcas: {len(brands)} | Precio: ${min(prices):,}-${max(prices):,} | Promedio: ${sum(prices)//len(prices):,}")


if __name__ == "__main__":
    main()
