"""
Marca PILOTO_OTERO=true en los registros de las agencias del piloto.
Idempotente. Re-ejecutable sin riesgo.
"""
import json, os, time, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
env = {}
for line in open(os.path.join(ROOT, ".env")):
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k] = v

PAT = env["AIRTABLE_PAT"]
BASE = env["AIRTABLE_BASE_ID"]
TABLE = env["AIRTABLE_TABLE_ID"]
API = f"https://api.airtable.com/v0/{BASE}/{TABLE}"
HEADERS = {"Authorization": f"Bearer {PAT}", "Content-Type": "application/json"}

PILOTO_AGENCIAS = [3852, 4199]
BATCH = 10
DELAY = 0.25


def http(method, url, body=None, retries=2):
    for attempt in range(retries + 1):
        try:
            data = json.dumps(body).encode("utf-8") if body else None
            req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8")
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP {e.code}: {err}")
    raise RuntimeError("retries exhausted")


def fetch_all_piloto():
    """Devuelve [(record_id, agencia_id, ya_marcado), ...] para los autos del piloto."""
    formula = f"OR({','.join(f'{{AGENCIA_ID}}={a}' for a in PILOTO_AGENCIAS)})"
    out = []
    offset = None
    while True:
        params = f"filterByFormula={urllib.parse.quote(formula)}&fields%5B%5D=AGENCIA_ID&fields%5B%5D=PILOTO_OTERO&pageSize=100"
        if offset:
            params += f"&offset={offset}"
        data = http("GET", f"{API}?{params}")
        for r in data.get("records", []):
            f = r.get("fields", {})
            out.append((r["id"], f.get("AGENCIA_ID"), bool(f.get("PILOTO_OTERO"))))
        offset = data.get("offset")
        if not offset:
            break
    return out


def main():
    import urllib.parse
    globals()["urllib"].parse = urllib.parse  # ensure quote available
    print("Buscando autos del piloto...")
    autos = fetch_all_piloto()
    print(f"  {len(autos)} autos encontrados")

    pendientes = [a for a in autos if not a[2]]
    ya = len(autos) - len(pendientes)
    print(f"  ya marcados: {ya}")
    print(f"  por marcar: {len(pendientes)}")

    if not pendientes:
        print("Nada que hacer.")
        return

    for i in range(0, len(pendientes), BATCH):
        chunk = pendientes[i : i + BATCH]
        body = {"records": [{"id": r[0], "fields": {"PILOTO_OTERO": True}} for r in chunk]}
        http("PATCH", API, body)
        time.sleep(DELAY)
        print(f"  marcados {min(i+BATCH, len(pendientes))}/{len(pendientes)}")

    print(f"\nListo. {len(pendientes)} registros marcados PILOTO_OTERO=true.")


if __name__ == "__main__":
    import urllib.parse  # noqa
    main()
