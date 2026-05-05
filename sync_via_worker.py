"""
Sube catalogo.json al Worker crm-sync-worker (que upsertea a D1
con D1 binding nativo). Reemplaza upsert_d1.py que usaba REST API
de Cloudflare con token externo.

Variables de entorno:
  - SYNC_WORKER_URL          default: https://crm-sync-worker.grupo-plasencia-automotriz.workers.dev/api/inventario/upsert
  - CATALOG_SYNC_SECRET      shared secret (header X-Sync-Secret)
"""

import json
import os
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))

# .env si existe (local)
env = {}
env_path = os.path.join(ROOT, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v

URL = env.get("SYNC_WORKER_URL") or os.environ.get("SYNC_WORKER_URL") \
      or "https://crm-sync-worker.grupo-plasencia-automotriz.workers.dev/api/inventario/upsert"
SECRET = env.get("CATALOG_SYNC_SECRET") or os.environ.get("CATALOG_SYNC_SECRET")

if not SECRET:
    raise SystemExit("CATALOG_SYNC_SECRET no esta seteado (env o .env)")


def main():
    print(f"Cargando catalogo.json...")
    with open(os.path.join(ROOT, "catalogo.json")) as f:
        catalogo = json.load(f)
    print(f"  {len(catalogo)} autos en feed")

    body = json.dumps({"records": catalogo}, ensure_ascii=False, default=str).encode("utf-8")
    print(f"Subiendo a {URL[:60]}... ({len(body)} bytes)")

    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json",
        "X-Sync-Secret": SECRET,
    }, method="POST")

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"HTTP {e.code}: {body}")

    elapsed = time.time() - t0
    print(f"\nResumen Worker (en {elapsed:.1f}s):")
    print(f"  success:     {data.get('success')}")
    print(f"  feed_count:  {data.get('feed_count')}")
    print(f"  upsert_ok:   {data.get('upsert_ok')}")
    print(f"  upsert_err:  {data.get('upsert_err')}")
    print(f"  bajas:       {data.get('bajas')}")
    if data.get('errores'):
        print(f"  errores muestra (max 10):")
        for e in data['errores']:
            print(f"    {e}")
        with open(os.path.join(ROOT, "errores_insercion.json"), "w") as f:
            json.dump(data['errores'], f, indent=2, ensure_ascii=False)

    if not data.get('success'):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
