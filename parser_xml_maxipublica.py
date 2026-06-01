"""
Parser XML del feed oficial de Maxipublica.

Fuente UNICA de verdad del catalogo Seminuevos Plasencia:
  https://inventory-feed.maxipublica.com/campaigns/xml/group/vehicle_feed_group_e1490ae1e92f.xml

Reemplaza al scraper anterior que pegaba al API JSON privado de la web.
Cero invenciones: solo lo que viene en campos estructurados del XML, mas
campos parseados con regex acotado del <description> donde el patron es
consistente y delimitado. Si un patron NO matchea exacto, el campo queda
vacio (None / ""), nunca un valor placeholder.

Output: catalogo.json compatible con el shape que consume crm-sync-worker.
"""

import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

# ---------- Config ----------
# 2026-05-23: Maxipublica migró el feed a su dominio propio (antes era el bucket
# S3 directo: maxipublica-inventory-feeds.s3.amazonaws.com). Mismo archivo, mismo
# contenido, host nuevo. Validado HTTP 200 + 3.2 MB el día del cambio.
FEED_URL = "https://inventory-feed.maxipublica.com/campaigns/xml/group/vehicle_feed_group_e1490ae1e92f.xml"

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOGO_JSON = os.path.join(OUT_DIR, "catalogo.json")
RESUMEN_FILE = os.path.join(OUT_DIR, "resumen_carga.txt")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PlasenciaCatalogSync/2.0)"}

# Mapeos XML → labels en español que pinta la landing.
# Si el feed introduce un valor nuevo no mapeado, se preserva el original
# (NO se inventa un mapeo silencioso).
TRANS_MAP = {
    "AUTOMATIC": "Automática",
    "MANUAL": "Manual",
    "CVT": "CVT",
    "DUAL_CLUTCH": "Doble embrague",
    "SEMI_AUTOMATIC": "Semi-automática",
}
FUEL_MAP = {
    "GASOLINE": "Gasolina",
    "DIESEL": "Diésel",
    "ELECTRIC": "Eléctrico",
    "HYBRID": "Híbrido",
    "PLUG_IN_HYBRID": "Híbrido enchufable",
    "FLEX": "Flex",
}
BODY_MAP = {
    "SEDAN": "Sedán",
    "HATCHBACK": "Hatchback",
    "SUV": "SUV",
    "CROSSOVER": "Crossover",
    "PICKUP": "Pickup",
    "TRUCK": "Pickup",
    "VAN": "Van",
    "MINIVAN": "Minivan",
    "COUPE": "Coupé",
    "CONVERTIBLE": "Convertible",
    "WAGON": "Wagon",
    "OTHER": "Otro",
}
DRIVETRAIN_MAP = {
    "FWD": "Delantera",
    "RWD": "Trasera",
    "AWD": "AWD",
    "4WD": "4x4",
    "4X4": "4x4",
}

# Agencias del piloto que se exponen en la landing del piloto.
#   3852 = Seminuevos Plasencia Lopez Mateos (Lote Otero, Fase 1).
#   4199, 3886, 3736, 3888, 3737, 3885 = las 6 sucursales Mazda del area
#   metropolitana de Guadalajara (Fase 2).
#   4054 = Seminuevos Plasencia Bugambilias (Fase 3, mayo 2026, +80 autos).
# Explicitamente EXCLUIDAS aunque sean Mazda: 3887 Mazda Vallarta y 3905
# Mazda Manzanillo (fuera de GDL metro).
# Mantener sincronizada con PILOTO_OTERO_AGENCIAS en crm-sync-worker/src/index.js.
PILOTO_AGENCIAS = {3852, 4199, 3886, 3736, 3888, 3737, 3885, 4054}


# ---------- Helpers ----------
def http_get(url, retries=2):
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            if attempt < retries:
                import time
                time.sleep(2)
    raise RuntimeError(f"GET fallo tras {retries+1} intentos: {url} -> {last_err}")


def parse_price(price_str):
    """ '364000 MXN' -> 364000.0  |  '' -> None  |  'lalala' -> None """
    if not price_str:
        return None
    m = re.search(r"[\d,]+(?:\.\d+)?", price_str)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def re_int(text, pattern):
    if not text:
        return None
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (ValueError, IndexError):
        return None


def re_float(text, pattern):
    if not text:
        return None
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (ValueError, IndexError):
        return None


def re_str(text, pattern):
    if not text:
        return ""
    m = re.search(pattern, text)
    return (m.group(1).strip() if m else "")


def parse_equipamiento(desc):
    """Extrae el bloque EQUIPAMIENTO del description.

    Formato esperado del feed:
      "...EQUIPAMIENTO:Audio: a, b, cConfort: x, y, zSeguridad: ... MP{vehicle_id}"

    Las secciones (Audio, Confort, Seguridad, Documentos, Eléctrico, Extra,
    Gadgets) vienen concatenadas sin separador entre la última palabra de
    una y el nombre de la siguiente. Como los nombres de seccion son
    palabras conocidas, los usamos como anchors para split.

    Si el bloque EQUIPAMIENTO no esta o no matchea, retorna {} — sin invento.
    """
    if not desc:
        return {}
    m = re.search(r"EQUIPAMIENTO\s*:(.+?)(?:\s*MP\d+)?\s*$", desc, re.DOTALL)
    if not m:
        return {}
    block = m.group(1)

    secciones = ["Audio", "Confort", "Documentos", "Eléctrico", "Electrico",
                 "Extra", "Gadgets", "Seguridad"]
    # Split por nombre de sección preservando los encabezados
    pattern = r"(" + "|".join(re.escape(s) for s in secciones) + r"):"
    parts = re.split(pattern, block)
    out = {}
    # parts = ['', 'Audio', ' a, b, c', 'Confort', ' x, y, z', ...]
    for i in range(1, len(parts) - 1, 2):
        nombre = parts[i].strip()
        contenido = parts[i + 1].strip()
        if not contenido:
            continue
        # Normaliza Eléctrico / Electrico
        if nombre == "Electrico":
            nombre = "Eléctrico"
        items = [x.strip() for x in contenido.split(",") if x.strip()]
        if items:
            out[nombre] = items
    return out


def parse_dealer(dealer_id):
    """ '3736-Mazda_Americas' -> (3736, 'Mazda Americas') """
    if not dealer_id:
        return (None, "")
    parts = dealer_id.split("-", 1)
    try:
        aid = int(parts[0])
    except (ValueError, IndexError):
        return (None, dealer_id)
    nombre = parts[1].replace("_", " ") if len(parts) > 1 else ""
    return (aid, nombre)


def first_image_url(listing):
    for img in listing.findall("image"):
        url = (img.findtext("url") or "").strip()
        if url:
            return url
    return ""


def all_image_urls(listing):
    urls = []
    for img in listing.findall("image"):
        url = (img.findtext("url") or "").strip()
        if url:
            urls.append(url)
    return urls


# ---------- Parser ----------
def parse_listing(listing):
    """Convierte un <listing> XML al shape consumible por crm-sync-worker.

    Cero invenciones: solo campos estructurados del XML + parsing acotado
    del <description>. Si un campo no esta presente o no matchea, queda
    vacio.
    """
    vehicle_id = listing.findtext("vehicle_id")
    if not vehicle_id:
        return None
    try:
        vid = int(vehicle_id)
    except ValueError:
        return None

    title = (listing.findtext("title") or "").strip()
    description = (listing.findtext("description") or "").strip()
    make = (listing.findtext("make") or "").strip()
    model = (listing.findtext("model") or "").strip()
    year = listing.findtext("year")
    try:
        year_int = int(year) if year else None
    except ValueError:
        year_int = None

    price_raw = (listing.findtext("price") or "").strip()
    precio = parse_price(price_raw)

    mileage_raw = (listing.findtext("mileage") or "").strip()
    odometro = None
    if mileage_raw:
        try:
            odometro = int(re.sub(r"[^\d]", "", mileage_raw))
        except ValueError:
            odometro = None

    transmission = (listing.findtext("transmission") or "").strip().upper()
    fuel = (listing.findtext("fuel_type") or "").strip().upper()
    body = (listing.findtext("body_style") or "").strip().upper()
    drivetrain = (listing.findtext("drivetrain") or "").strip().upper()
    condition = (listing.findtext("condition") or "").strip()
    color_ext = (listing.findtext("exterior_color") or "").strip()

    availability = (listing.findtext("availability") or "").strip().lower()
    state = (listing.findtext("state_of_vehicle") or "").strip().upper()

    # VIN (Vehicle Identification Number). En el feed XML viene como <vin>...</vin>.
    # Coverage real ~61% global, >95% en Bugambilias / Lopez Mateos / Mazda Plasencia / Acueducto.
    # Sucursales sin VIN (Galerias / Americas / Gonzalez Gallo) emiten <vin></vin> — queda None.
    vin = (listing.findtext("vin") or "").strip().upper()
    if not vin or len(vin) < 11:  # VIN válido ISO 3779 = 17 chars; aceptamos >=11 por tolerancia
        vin = None

    dealer_id_raw = (listing.findtext("dealer_id") or "").strip()
    agencia_id, agencia_nombre = parse_dealer(dealer_id_raw)
    dealer_phone = (listing.findtext("dealer_phone") or "").strip()

    lat = (listing.findtext("latitude") or "").strip()
    lng = (listing.findtext("longitude") or "").strip()
    coords = f"{lat},{lng}" if (lat and lng) else ""

    images = all_image_urls(listing)
    thumbnail = images[0] if images else ""

    url_detalle = (listing.findtext("url") or "").strip()

    # Datos parseados del <description> con regex acotados.
    # Si el patron no matchea EXACTO, el campo queda vacio.
    color_int = re_str(description, r"Color interior:\s*([A-Za-zÁÉÍÓÚáéíóúÑñ]+(?:\s+[A-Za-zÁÉÍÓÚáéíóúÑñ]+)?)")
    puertas = re_int(description, r"Puertas:\s*(\d+)")
    pasajeros = re_int(description, r"Pasajeros:\s*(\d+)")
    velocidades = re_int(description, r"Velocidades:\s*(\d+)")
    cilindros = re_int(description, r"Cilindros:\s*(\d+)")
    potencia_hp = re_int(description, r"Potencia en HP:\s*(\d+)")
    tanque_l = re_int(description, r"Capacidad del tanque \(l\):\s*(\d+)")
    consumo_combinado = re_float(description, r"Consumo en combinado \(Km/l\):\s*([\d.]+)")
    consumo_ciudad = re_float(description, r"Consumo en ciudad \(Km/l\):\s*([\d.]+)")
    consumo_carretera = re_float(description, r"Consumo en carretera \(Km/l\):\s*([\d.]+)")
    rines_pulgadas = re_float(description, r"Diámetro de rines \(pulgadas\):\s*([\d.]+)")

    equipamiento = parse_equipamiento(description)

    return {
        # Campos directos del XML (estructurados)
        "ID_AUTO": vid,
        "VIN": vin,  # 17-char ISO 3779. None si feed lo trae vacío.
        "TITULO": title,
        "MARCA": make,
        "MODELO": model,
        "ANIO": year_int,
        "TRIM": "",  # NO existe estructurado en el feed XML — se omite por decision explicita.
        "PRECIO": precio,
        "ODOMETRO_KM": odometro,
        "CONDICION": condition,
        "COLOR_EXT": color_ext,
        "TRANSMISION": TRANS_MAP.get(transmission, transmission.title() if transmission else ""),
        "TRACCION": DRIVETRAIN_MAP.get(drivetrain, drivetrain if drivetrain else ""),
        "COMBUSTIBLE": FUEL_MAP.get(fuel, fuel.title() if fuel else ""),
        "SEGMENTO": BODY_MAP.get(body, body.title() if body else ""),
        "AGENCIA_ID": agencia_id,
        "AGENCIA_NOMBRE": agencia_nombre,
        "AGENCIA_TELEFONO": dealer_phone,
        "URL_DETALLE": url_detalle,
        "COORDENADAS_GPS": coords,
        "THUMBNAIL": thumbnail,
        "IMAGENES_URLS": images,
        "TOTAL_IMAGENES": len(images),
        "AVAILABILITY": availability,
        "STATE_OF_VEHICLE": state,

        # Campos parseados del <description> con regex acotado.
        # Si el patron no matchea, queda None / "" — nunca placeholder.
        "COLOR_INT": color_int,
        "PUERTAS": puertas,
        "PASAJEROS": pasajeros,
        "VELOCIDADES": velocidades,
        "CILINDROS": cilindros,
        "POTENCIA_HP": potencia_hp,
        "TANQUE_L": tanque_l,
        "CONSUMO_COMBINADO": consumo_combinado,
        "CONSUMO_CIUDAD": consumo_ciudad,
        "CONSUMO_CARRETERA": consumo_carretera,
        "RINES_PULGADAS": rines_pulgadas,
        "EQUIPAMIENTO": equipamiento,  # dict {Audio:[...], Confort:[...], ...}

        # Metadata del proceso
        "ACTIVO": availability == "available",
        "FECHA_SCRAPE": datetime.now().isoformat(),
        "FECHA_UPDATE_ORIGEN": "",  # El feed XML no expone fecha por listing.
    }


# ---------- Pipeline ----------
def fetch_xml():
    print(f"[1/3] Descargando feed XML")
    print(f"      {FEED_URL}")
    raw = http_get(FEED_URL)
    print(f"      {len(raw):,} bytes recibidos")
    return raw


def parse_all(raw_xml):
    print("[2/3] Parseando XML")
    root = ET.fromstring(raw_xml)
    listings = root.findall("listing")
    print(f"      {len(listings)} <listing> encontrados")

    catalogo = []
    sin_id = 0
    for listing in listings:
        record = parse_listing(listing)
        if record is None:
            sin_id += 1
            continue
        catalogo.append(record)
    print(f"      {len(catalogo)} normalizados | {sin_id} omitidos sin id")
    return catalogo


def escribir_outputs(catalogo):
    print("[3/3] Escribiendo outputs")
    with open(CATALOGO_JSON, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=2)
    print(f"      {CATALOGO_JSON} ({os.path.getsize(CATALOGO_JSON)/1024:.1f} KB)")

    # Resumen humano
    from collections import Counter
    marcas = Counter(c["MARCA"] for c in catalogo if c["MARCA"])
    agencias = Counter((c["AGENCIA_ID"], c["AGENCIA_NOMBRE"]) for c in catalogo)
    anios = [c["ANIO"] for c in catalogo if c["ANIO"]]
    precios = [c["PRECIO"] for c in catalogo if c["PRECIO"]]
    piloto = sum(1 for c in catalogo if c["AGENCIA_ID"] in PILOTO_AGENCIAS)

    lines = [
        "Catálogo Seminuevos Plasencia",
        f"Fecha ejecución: {datetime.now().isoformat()}",
        f"Fuente: {FEED_URL}",
        f"Total listings: {len(catalogo)}",
        f"Del piloto Otero: {piloto} (agencias {sorted(PILOTO_AGENCIAS)})",
    ]
    if anios:
        lines.append(f"Años: {min(anios)}–{max(anios)}")
    if precios:
        lines.append(f"Precios: ${min(precios):,.0f}–${max(precios):,.0f}")
    lines.append("")
    lines.append(f"Marcas ({len(marcas)}):")
    for marca, n in marcas.most_common():
        lines.append(f"  {marca}: {n}")
    lines.append("")
    lines.append(f"Agencias ({len(agencias)}):")
    for (aid, nombre), n in sorted(agencias.items(), key=lambda x: -x[1]):
        flag = "★" if aid in PILOTO_AGENCIAS else " "
        lines.append(f"  {flag} [{aid}] {nombre}: {n}")

    resumen = "\n".join(lines)
    with open(RESUMEN_FILE, "w", encoding="utf-8") as f:
        f.write(resumen)
    print(f"      {RESUMEN_FILE}")
    print()
    print(resumen)


def main():
    raw = fetch_xml()
    catalogo = parse_all(raw)
    if not catalogo:
        print("ERROR: catalogo vacio. No se escriben outputs.", file=sys.stderr)
        sys.exit(1)
    escribir_outputs(catalogo)
    print("\nListo.")


if __name__ == "__main__":
    main()
