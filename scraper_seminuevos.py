"""
Scraper Catalogo Seminuevos Plasencia
Fuente: seminuevosplasencia.com (Maxipublica / Angular SPA)
Output: catalogo.json (fuente de datos del micrositio) + catalogo.csv + resumen
"""

import json
import csv
import time
import urllib.request
import urllib.error
import os
from datetime import datetime

# ---------- Config ----------
BASE_URL = "https://www.seminuevosplasencia.com"
LISTING_URL = f"{BASE_URL}/api/inventory"
DETAIL_URL = f"{BASE_URL}/api/inventory/{{}}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": f"{BASE_URL}/inventario",
}
DELAY = 0.4
RETRY_DELAY = 1.0
CHECKPOINT_EVERY = 50

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_FILE = os.path.join(OUT_DIR, "_checkpoint_detalles.json")
CATALOGO_JSON = os.path.join(OUT_DIR, "catalogo.json")
CATALOGO_CSV = os.path.join(OUT_DIR, "catalogo.csv")
RESUMEN_FILE = os.path.join(OUT_DIR, "resumen_carga.txt")


# ---------- HTTP ----------
def http_get_json(url, retries=2):
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(RETRY_DELAY)
    raise RuntimeError(f"GET fallo tras {retries+1} intentos: {url} -> {last_err}")


# ---------- Normalizacion ----------
def normalizar(detalle, fecha_update_origen):
    if not isinstance(detalle, dict) or "id" not in detalle:
        return None

    attrs = {a.get("id"): a.get("value") for a in detalle.get("attributes", []) or []}
    loc = detalle.get("location", {}) or {}
    loc_inner = loc.get("location", {}) or {}
    seller = detalle.get("seller", {}) or {}
    images = detalle.get("images", []) or []

    odometer = None
    for a in (detalle.get("mainAttributes", {}) or {}).get("odometerGroup", []) or []:
        if a.get("id") == "odometer":
            odometer = a.get("value")
            break

    geo = loc.get("geoReference", "") or ""
    if geo:
        parts = geo.split(",")
        geo = ",".join(parts[:2]) if len(parts) >= 2 else geo

    phone = ""
    if seller.get("phone"):
        try:
            phone = seller["phone"][0].get("number", "") or ""
        except (IndexError, AttributeError):
            phone = ""

    def _int(v):
        try:
            return int(v) if v not in (None, "") else None
        except (ValueError, TypeError):
            return None

    def _num(v):
        try:
            return float(v) if v not in (None, "") else None
        except (ValueError, TypeError):
            return None

    return {
        "ID_AUTO": detalle.get("id"),
        "TITULO": detalle.get("title", "") or "",
        "MARCA": attrs.get("brand", "") or "",
        "MODELO": attrs.get("model", "") or "",
        "ANIO": _int(attrs.get("year")),
        "TRIM": attrs.get("trim", "") or "",
        "PRECIO": _num(attrs.get("price")),
        "ODOMETRO_KM": _num(odometer),
        "CONDICION": detalle.get("condition", "") or "",
        "COLOR_EXT": attrs.get("colorExt", "") or "",
        "COLOR_INT": attrs.get("colorInt", "") or "",
        "TRANSMISION": attrs.get("transmission", "") or "",
        "TRACCION": attrs.get("traction", "") or "",
        "COMBUSTIBLE": attrs.get("energy", "") or "",
        "SEGMENTO": attrs.get("bodyType", "") or "",
        "PUERTAS": _int(attrs.get("doors")),
        "PASAJEROS": _int(attrs.get("passengers")),
        "CILINDROS": _int(attrs.get("cylinders")),
        "POTENCIA_HP": _num(attrs.get("power")),
        "TORQUE": _num(attrs.get("torque")),
        "VELOCIDADES": _int(attrs.get("speeds")),
        "TANQUE_L": _num(attrs.get("tankCapacity")),
        "CONSUMO_CIUDAD": _num(attrs.get("consumptionCity")),
        "CONSUMO_CARRETERA": _num(attrs.get("consumptionRoad")),
        "CONSUMO_COMBINADO": _num(attrs.get("consumptionCombined")),
        "RINES_PULGADAS": _num(attrs.get("diameterWheels")),
        "LARGO_MM": _num(attrs.get("dimensionsLength")),
        "ALTO_MM": _num(attrs.get("dimensionsHeight")),
        "PESO_KG": _num(attrs.get("dimensionsWeight")),
        "AIRE_ACONDICIONADO": attrs.get("climate", "") or "",
        "DIRECCION": attrs.get("direction", "") or "",
        "VESTIDURA": attrs.get("vesture", "") or "",
        "GARANTIA": attrs.get("waranty", "") or "",
        "EQUIPAMIENTO_RESUMEN": attrs.get("descriptionAut", "") or "",
        "AGENCIA_NOMBRE": seller.get("commercialName", "") or "",
        "AGENCIA_ID": detalle.get("sellerId"),
        "AGENCIA_TELEFONO": phone,
        "CALLE": loc.get("street", "") or "",
        "NUM_EXT": loc.get("numExt", "") or "",
        "COLONIA": (loc_inner.get("neighborhood") or {}).get("name", "") or "",
        "CIUDAD": (loc_inner.get("city") or {}).get("name", "") or "",
        "ESTADO": (loc_inner.get("state") or {}).get("name", "") or "",
        "CP": loc.get("zipCode", "") or "",
        "COORDENADAS_GPS": geo,
        "THUMBNAIL": detalle.get("thumbnail", "") or "",
        "IMAGENES_URLS": [img.get("url", "") for img in images if img.get("url")],
        "TOTAL_IMAGENES": len(images),
        "URL_DETALLE": (lambda u: ("" if not u else (u if u.startswith("http") else "https://" + u)))(detalle.get("inventoryLink", "")),
        "ACTIVO": True,
        "FECHA_SCRAPE": datetime.now().isoformat(),
        "FECHA_UPDATE_ORIGEN": fecha_update_origen,
    }


# ---------- Pipeline ----------
def fetch_listing():
    print(f"[1/4] Listing: {LISTING_URL}")
    data = http_get_json(LISTING_URL)
    items = data.get("results", []) or []
    ids = [it["id"] for it in items if isinstance(it, dict) and it.get("id")]
    fecha_update = (data.get("details") or {}).get("dateUpdate") or ""
    print(f"      {len(ids)} IDs encontrados | dateUpdate: {fecha_update}")
    return ids, fecha_update


def fetch_detalles(ids):
    detalles = {}
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            detalles = json.load(f)
        print(f"[2/4] Checkpoint: {len(detalles)} detalles ya descargados")

    pendientes = [i for i in ids if str(i) not in detalles]
    print(f"[2/4] Detalles a descargar: {len(pendientes)}")

    for idx, _id in enumerate(pendientes, 1):
        try:
            detalles[str(_id)] = http_get_json(DETAIL_URL.format(_id))
        except Exception as e:
            detalles[str(_id)] = {"_error": str(e), "id": _id}
            print(f"      ! ID {_id}: {e}")

        if idx % CHECKPOINT_EVERY == 0:
            with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
                json.dump(detalles, f, ensure_ascii=False)
            print(f"      progreso: {idx}/{len(pendientes)} (checkpoint)")

        time.sleep(DELAY)

    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(detalles, f, ensure_ascii=False)
    return detalles


def construir_catalogo(detalles, fecha_update):
    print("[3/4] Normalizando")
    catalogo = []
    errores = 0
    for _id, det in detalles.items():
        if isinstance(det, dict) and det.get("_error"):
            errores += 1
            continue
        norm = normalizar(det, fecha_update)
        if norm:
            catalogo.append(norm)
    print(f"      {len(catalogo)} normalizados | {errores} con error")
    return catalogo, errores


def escribir_outputs(catalogo, errores, fecha_update):
    print("[4/4] Escribiendo outputs")

    with open(CATALOGO_JSON, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=2)
    print(f"      {CATALOGO_JSON}")

    if catalogo:
        cols = list(catalogo[0].keys())
        with open(CATALOGO_CSV, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in catalogo:
                row_csv = {
                    k: (", ".join(v) if isinstance(v, list) else v) for k, v in row.items()
                }
                w.writerow(row_csv)
        print(f"      {CATALOGO_CSV}")

    # Resumen
    from collections import Counter
    marcas = Counter(c["MARCA"] for c in catalogo if c["MARCA"])
    agencias = {}
    for c in catalogo:
        key = (c["AGENCIA_ID"], c["AGENCIA_NOMBRE"], c["CIUDAD"])
        agencias[key] = agencias.get(key, 0) + 1
    anios = [c["ANIO"] for c in catalogo if c["ANIO"]]
    precios = [c["PRECIO"] for c in catalogo if c["PRECIO"]]

    lines = []
    lines.append(f"Catalogo Seminuevos Plasencia")
    lines.append(f"Fecha ejecucion: {datetime.now().isoformat()}")
    lines.append(f"Fecha update origen: {fecha_update}")
    lines.append(f"Total normalizados: {len(catalogo)}")
    lines.append(f"Errores: {errores}")
    if anios:
        lines.append(f"Rango anios: {min(anios)} - {max(anios)}")
    if precios:
        lines.append(
            f"Rango precios: ${min(precios):,.0f} - ${max(precios):,.0f}"
        )
    lines.append("")
    lines.append(f"Marcas ({len(marcas)}):")
    for marca, n in marcas.most_common():
        lines.append(f"  {marca}: {n}")
    lines.append("")
    lines.append(f"Agencias ({len(agencias)}):")
    for (aid, nombre, ciudad), n in sorted(agencias.items(), key=lambda x: -x[1]):
        lines.append(f"  [{aid}] {nombre} | {ciudad} | {n} autos")

    resumen = "\n".join(lines)
    with open(RESUMEN_FILE, "w", encoding="utf-8") as f:
        f.write(resumen)
    print(f"      {RESUMEN_FILE}")
    print()
    print(resumen)


def main():
    ids, fecha_update = fetch_listing()
    detalles = fetch_detalles(ids)
    catalogo, errores = construir_catalogo(detalles, fecha_update)
    escribir_outputs(catalogo, errores, fecha_update)
    print("\nListo.")


if __name__ == "__main__":
    main()
