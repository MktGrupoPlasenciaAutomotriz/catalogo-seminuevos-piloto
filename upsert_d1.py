"""
Upsert catalogo.json -> D1 inventario_seminuevos (Cloudflare).

Reemplaza upsert_airtable.py. Cero dependencia de Airtable.

Logica:
- Lee catalogo.json (output del scraper)
- Para cada auto: INSERT ... ON CONFLICT(id_auto) DO UPDATE SET ...
  - Conserva campos editables manuales (DESTACADO, NOTAS_INTERNAS, FECHA_BAJA)
    no sobreescribiendolos en el UPDATE (sentencia COALESCE)
- Al terminar: marca activo=0 + fecha_baja=hoy a los que ya no llegaron en el feed
- Setea piloto_otero=1 si agencia_id en alcance del piloto

Variables de entorno requeridas (en .env o GH Actions secrets):
  - CLOUDFLARE_API_TOKEN  (scope D1:Edit)
  - CF_ACCOUNT_ID         (literal: 3a73c6035cae8b2bfab359a16ec44fca)
  - D1_DATABASE_ID        (literal: ad3e7ad8-55e0-4436-941a-6e299638af8e)
"""

import json
import os
import time
import urllib.request
import urllib.error
from datetime import date, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))

# Cargar .env manual (sin dependencias)
env = {}
env_path = os.path.join(ROOT, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v

# GitHub Actions inyecta como variables de proceso
TOKEN = env.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
ACCOUNT_ID = env.get("CF_ACCOUNT_ID") or os.environ.get("CF_ACCOUNT_ID") or "3a73c6035cae8b2bfab359a16ec44fca"
DB_ID = env.get("D1_DATABASE_ID") or os.environ.get("D1_DATABASE_ID") or "ad3e7ad8-55e0-4436-941a-6e299638af8e"

if not TOKEN:
    raise SystemExit("CLOUDFLARE_API_TOKEN no esta seteado (env o .env)")

API_URL = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/d1/database/{DB_ID}/query"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

# Alcance del piloto (mismo que en upsert_airtable.py original)
PILOTO_OTERO_AGENCIAS = {3852, 4199, 3886, 3736, 3888, 3737, 3885}

# Columnas dedicadas en inventario_seminuevos (resto va a data_json).
COLS_DEDICADAS = {
    "id_auto", "marca", "modelo", "anio", "trim", "segmento", "titulo",
    "precio", "condicion", "odometro_km",
    "transmision", "combustible", "cilindros", "potencia_hp",
    "color_ext", "color_int", "pasajeros", "puertas", "rines_pulgadas",
    "thumbnail", "total_imagenes",
    "agencia_id", "agencia_nombre", "agencia_telefono", "ciudad", "estado",
    "url_detalle",
    "fecha_scrape", "fecha_update_origen",
}


def http_request(body, retries=2):
    last_err = None
    for attempt in range(retries + 1):
        try:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(API_URL, data=data, headers=HEADERS, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.read().decode('utf-8')}"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(last_err)
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = str(e)
            time.sleep(1)
    raise RuntimeError(f"Request fallo: {last_err}")


def d1_query(sql, params=None):
    body = {"sql": sql, "params": params or []}
    return http_request(body)


def normaliza_fecha_origen(v):
    """FECHA_UPDATE_ORIGEN viene como dict {'date': '...'} en algunos casos."""
    if isinstance(v, dict):
        return v.get("date") or ""
    return v or ""


def split_dedicado_y_resto(auto):
    """Separa los campos del scraper en (cols_dedicados, resto_para_data_json).
    Las claves del scraper estan en MAYUSCULAS; las cols D1 en minusculas."""
    dedicado = {}
    resto = {}
    for k, v in auto.items():
        col = k.lower()
        if col in COLS_DEDICADAS:
            if col == "fecha_update_origen":
                v = normaliza_fecha_origen(v)
            dedicado[col] = v
        else:
            resto[k] = v
    return dedicado, resto


def upsert_uno(auto):
    """INSERT ... ON CONFLICT DO UPDATE preservando editables manuales."""
    dedicado, resto = split_dedicado_y_resto(auto)
    id_auto = dedicado.get("id_auto")
    if not id_auto:
        return False

    piloto = 1 if dedicado.get("agencia_id") in PILOTO_OTERO_AGENCIAS else 0
    data_json = json.dumps(resto, ensure_ascii=False, default=str)

    # SQLite UPSERT: insert con todas las cols + on conflict update.
    # NO incluyo destacado, notas_internas, fecha_baja en el UPDATE — son editables manuales.
    sql = """
    INSERT INTO inventario_seminuevos (
      id_auto, marca, modelo, anio, trim, segmento, titulo,
      precio, condicion, odometro_km,
      transmision, combustible, cilindros, potencia_hp,
      color_ext, color_int, pasajeros, puertas, rines_pulgadas,
      thumbnail, total_imagenes,
      agencia_id, agencia_nombre, agencia_telefono, ciudad, estado,
      url_detalle,
      piloto_otero, activo,
      fecha_scrape, fecha_update_origen,
      data_json,
      updated_at
    ) VALUES (
      ?, ?, ?, ?, ?, ?, ?,
      ?, ?, ?,
      ?, ?, ?, ?,
      ?, ?, ?, ?, ?,
      ?, ?,
      ?, ?, ?, ?, ?,
      ?,
      ?, 1,
      ?, ?,
      ?,
      CURRENT_TIMESTAMP
    )
    ON CONFLICT(id_auto) DO UPDATE SET
      marca=excluded.marca, modelo=excluded.modelo, anio=excluded.anio,
      trim=excluded.trim, segmento=excluded.segmento, titulo=excluded.titulo,
      precio=excluded.precio, condicion=excluded.condicion, odometro_km=excluded.odometro_km,
      transmision=excluded.transmision, combustible=excluded.combustible,
      cilindros=excluded.cilindros, potencia_hp=excluded.potencia_hp,
      color_ext=excluded.color_ext, color_int=excluded.color_int,
      pasajeros=excluded.pasajeros, puertas=excluded.puertas, rines_pulgadas=excluded.rines_pulgadas,
      thumbnail=excluded.thumbnail, total_imagenes=excluded.total_imagenes,
      agencia_id=excluded.agencia_id, agencia_nombre=excluded.agencia_nombre,
      agencia_telefono=excluded.agencia_telefono, ciudad=excluded.ciudad, estado=excluded.estado,
      url_detalle=excluded.url_detalle,
      piloto_otero=excluded.piloto_otero,
      activo=1,
      fecha_baja=NULL,
      fecha_scrape=excluded.fecha_scrape, fecha_update_origen=excluded.fecha_update_origen,
      data_json=excluded.data_json,
      updated_at=CURRENT_TIMESTAMP
    """
    params = [
        id_auto,
        dedicado.get("marca", ""), dedicado.get("modelo"), dedicado.get("anio"),
        dedicado.get("trim"), dedicado.get("segmento"), dedicado.get("titulo"),
        dedicado.get("precio", 0), dedicado.get("condicion"), dedicado.get("odometro_km"),
        dedicado.get("transmision"), dedicado.get("combustible"),
        dedicado.get("cilindros"), dedicado.get("potencia_hp"),
        dedicado.get("color_ext"), dedicado.get("color_int"),
        dedicado.get("pasajeros"), dedicado.get("puertas"), dedicado.get("rines_pulgadas"),
        dedicado.get("thumbnail"), dedicado.get("total_imagenes", 0),
        dedicado.get("agencia_id"), dedicado.get("agencia_nombre"),
        dedicado.get("agencia_telefono"), dedicado.get("ciudad"), dedicado.get("estado"),
        dedicado.get("url_detalle"),
        piloto,
        dedicado.get("fecha_scrape"), dedicado.get("fecha_update_origen"),
        data_json,
    ]
    d1_query(sql, params)
    return True


def marcar_bajas():
    """Autos que NO se tocaron en este sync (updated_at < hoy GDL) y siguen activo=1
    -> activo=0 + fecha_baja=hoy. Aprovechamos que cada upsert pone updated_at=CURRENT_TIMESTAMP.

    Evita el limite de SQLite ~999 vars que se rompia con NOT IN (lista de 684 ids)."""
    hoy = date.today().isoformat()
    sql = """
      UPDATE inventario_seminuevos
      SET activo = 0,
          fecha_baja = CASE WHEN fecha_baja IS NULL THEN ? ELSE fecha_baja END,
          updated_at = CURRENT_TIMESTAMP
      WHERE activo = 1
        AND date(updated_at) < ?
    """
    res = d1_query(sql, [hoy, hoy])
    try:
        return res["result"][0]["meta"]["changes"]
    except (KeyError, IndexError, TypeError):
        return 0


def main():
    print("Cargando catalogo.json...")
    with open(os.path.join(ROOT, "catalogo.json")) as f:
        catalogo = json.load(f)
    print(f"  {len(catalogo)} autos en feed")

    print(f"Upsert a D1 ({DB_ID[:8]}...)...")
    ok = 0
    err = []
    t0 = time.time()
    for i, auto in enumerate(catalogo):
        try:
            if upsert_uno(auto):
                ok += 1
        except Exception as e:
            err.append({"id_auto": auto.get("ID_AUTO"), "error": str(e)[:200]})
            if len(err) <= 3:
                print(f"  ! {auto.get('ID_AUTO')}: {e}")
        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            print(f"  progreso: {i+1}/{len(catalogo)}  ok={ok} err={len(err)}  {elapsed:.0f}s")

    print(f"\nUpsert: ok={ok} err={len(err)}  ({time.time()-t0:.0f}s)")

    if err:
        with open(os.path.join(ROOT, "errores_insercion.json"), "w") as f:
            json.dump(err, f, indent=2, ensure_ascii=False)
        print(f"  errores -> errores_insercion.json")

    print("\nMarcando autos que ya no estan en el feed (updated_at < hoy)...")
    bajas = marcar_bajas()
    print(f"  marcados de baja: {bajas}")

    print(f"\nResumen:")
    print(f"  Feed:        {len(catalogo)}")
    print(f"  Upserts ok:  {ok}")
    print(f"  Errores:     {len(err)}")
    print(f"  Dados baja:  {bajas}")


if __name__ == "__main__":
    main()
