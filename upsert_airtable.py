"""
Upsert catalogo.json -> Airtable (Inventario)
Usa REST API directa con PAT (carga inicial masiva).
Para syncs incrementales diarios usar el MCP de Airtable.

Logica:
- Lee airtable_config.json y .env
- Lee catalogo.json
- Para cada batch de 10:
    - performUpsert con fieldsToMergeOn=["ID_AUTO"]
    - Airtable hace match por ID_AUTO: existe -> update, no existe -> create
    - Campos editables (DESTACADO, NOTAS_INTERNAS) NO se envian, asi sobreviven
- Al terminar: marca ACTIVO=false los autos que no llegaron en este sync
"""

import json
import os
import time
import urllib.request
import urllib.error
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))

# Cargar .env manual (sin dependencias)
env = {}
with open(os.path.join(ROOT, ".env")) as f:
    for line in f:
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v

PAT = env["AIRTABLE_PAT"]
BASE_ID = env["AIRTABLE_BASE_ID"]
TABLE_ID = env["AIRTABLE_TABLE_ID"]

API_URL = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
HEADERS = {
    "Authorization": f"Bearer {PAT}",
    "Content-Type": "application/json",
}

CAMPOS_EDITABLES = {"DESTACADO", "NOTAS_INTERNAS", "FECHA_BAJA"}
BATCH_SIZE = 10
DELAY = 0.25

# Alcance del piloto MVP.
# Fase 1 (lote Otero): Av. Lopez Mateos Sur 2600 / Mariano Otero, Jardines del Sol
#   - 3852 Seminuevos Plasencia Lopez Mateos (multimarca)
#   - 4199 Mazda Plasencia (Mariano Otero, contigua)
# Fase 2 (extension Mazda GDL metro, solicitada por Flor Alcaraz - abril 2026):
#   - 3886 Mazda Galerias (Zapopan)
#   - 3736 Mazda Americas (Guadalajara)
#   - 3888 Mazda Acueducto (Zapopan)
#   - 3737 Mazda Gonzalez Gallo (Guadalajara)
#   - 3885 Mazda Santa Anita (Tlajomulco)
PILOTO_OTERO_AGENCIAS = {3852, 4199, 3886, 3736, 3888, 3737, 3885}


def http_request(method, url, body=None, retries=2):
    last_err = None
    for attempt in range(retries + 1):
        try:
            data = json.dumps(body).encode("utf-8") if body else None
            req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            last_err = f"HTTP {e.code}: {err_body}"
            if e.code == 429:  # rate limit
                time.sleep(2 ** attempt)
                continue
            if e.code >= 500:
                time.sleep(1)
                continue
            raise RuntimeError(last_err)
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = str(e)
            time.sleep(1)
    raise RuntimeError(f"Request fallo: {last_err}")


def limpiar_record(auto):
    """Quita None, vacios, campos editables, y formatea para Airtable.
    Inyecta PILOTO_OTERO segun AGENCIA_ID."""
    fields = {}
    # Mantener PILOTO_OTERO sincronizado con el alcance del piloto
    fields["PILOTO_OTERO"] = auto.get("AGENCIA_ID") in PILOTO_OTERO_AGENCIAS
    for k, v in auto.items():
        if k in CAMPOS_EDITABLES:
            continue
        if v is None or v == "":
            continue
        if isinstance(v, dict):
            v = v.get("date") or v.get("value") or ""
            if not v:
                continue
        if isinstance(v, list):
            if not v:
                continue
            if k == "IMAGENES_URLS":
                v = "\n".join(v)
            else:
                v = ", ".join(str(x) for x in v)
        # URLs invalidas (sin http) -> omitir
        if k in ("THUMBNAIL", "URL_DETALLE"):
            if not isinstance(v, str) or not v.startswith("http"):
                continue
        fields[k] = v
    return {"fields": fields}


def upsert_batch(records):
    body = {
        "performUpsert": {"fieldsToMergeOn": ["ID_AUTO"]},
        "records": records,
        "typecast": True,
    }
    return http_request("PATCH", API_URL, body)


def fetch_all_existing_ids():
    """Devuelve dict {ID_AUTO: airtable_record_id} de todos los registros actuales."""
    out = {}
    offset = None
    while True:
        url = f"{API_URL}?fields%5B%5D=ID_AUTO&pageSize=100"
        if offset:
            url += f"&offset={offset}"
        data = http_request("GET", url)
        for r in data.get("records", []):
            id_auto = r.get("fields", {}).get("ID_AUTO")
            if id_auto is not None:
                out[int(id_auto)] = r["id"]
        offset = data.get("offset")
        if not offset:
            break
    return out


def marcar_bajas(ids_actuales_en_feed, existentes_antes):
    """Autos que estaban en Airtable pero ya no llegaron en el feed -> ACTIVO=false + FECHA_BAJA."""
    hoy = date.today().isoformat()
    a_dar_baja = []
    for id_auto, rec_id in existentes_antes.items():
        if id_auto not in ids_actuales_en_feed:
            a_dar_baja.append(
                {"id": rec_id, "fields": {"ACTIVO": False, "FECHA_BAJA": hoy}}
            )
    print(f"  Autos a marcar como baja: {len(a_dar_baja)}")
    for i in range(0, len(a_dar_baja), BATCH_SIZE):
        batch = a_dar_baja[i : i + BATCH_SIZE]
        http_request("PATCH", API_URL, {"records": batch})
        time.sleep(DELAY)
    return len(a_dar_baja)


def main():
    print("Cargando catalogo.json...")
    with open(os.path.join(ROOT, "catalogo.json")) as f:
        catalogo = json.load(f)
    print(f"  {len(catalogo)} autos")

    print("Snapshot de IDs existentes en Airtable...")
    existentes_antes = fetch_all_existing_ids()
    print(f"  {len(existentes_antes)} registros previos")

    print(f"Upsert en batches de {BATCH_SIZE}...")
    records = [limpiar_record(a) for a in catalogo]
    creados = 0
    actualizados = 0
    errores = []

    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        try:
            resp = upsert_batch(batch)
            c = len(resp.get("createdRecords", []))
            u = len(resp.get("updatedRecords", []))
            creados += c
            actualizados += u
        except Exception as e:
            errores.append({"batch_start": i, "error": str(e)})
            print(f"  ! batch {i}: {e}")

        if (i // BATCH_SIZE + 1) % 10 == 0:
            print(f"  progreso: {i + len(batch)}/{len(records)}  (c:{creados} u:{actualizados})")

        time.sleep(DELAY)

    print(f"\nUpsert terminado: creados={creados}  actualizados={actualizados}  errores={len(errores)}")

    print("\nMarcando autos que ya no estan en el feed...")
    ids_feed = {a["ID_AUTO"] for a in catalogo if a.get("ID_AUTO")}
    bajas = marcar_bajas(ids_feed, existentes_antes)

    if errores:
        with open(os.path.join(ROOT, "errores_insercion.json"), "w") as f:
            json.dump(errores, f, indent=2)
        print(f"\n! errores guardados en errores_insercion.json")

    print(f"\nResumen:")
    print(f"  Total en feed: {len(catalogo)}")
    print(f"  Creados: {creados}")
    print(f"  Actualizados: {actualizados}")
    print(f"  Marcados baja: {bajas}")
    print(f"  Errores: {len(errores)}")


if __name__ == "__main__":
    main()
