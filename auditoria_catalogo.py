#!/usr/bin/env python3
"""Auditoria de paridad del catalogo Seminuevos.

REGLA CANONICA (Chucho, 28-ago-2026):
    El catalogo publicado en seminuevos.grupoplasencia.com debe ser IDENTICO
    a lo que Maxipublica manda PARA LAS SUCURSALES DEL PILOTO. Ni mas ni menos.

    "debe ser identico a las sucursales que tenemos y ya"

No aplica al feed completo: el feed trae 690 vehiculos de 23 agencias y el
piloto publica solo 8 (~240). Comparar contra el total da un falso rojo.

Cadena que se audita:
    Maxipublica XML  ->  D1 inventario_seminuevos  ->  catalogo-piloto.json
                                                          (lo que ve el usuario)

Cada eslabon puede romperse distinto:
  - feed -> D1   : el sync no corrio, o el upsert fallo
  - D1 -> sitio  : el export corrio pero no se publico el commit
  - VIN          : cobertura del origen. NO es un defecto nuestro, pero se
                   mide aqui porque el VIN ancla el Caso A de atribucion y sin
                   el solo se puede atribuir por datos de la persona.

Uso:
    python3 auditoria_catalogo.py           # sale != 0 si hay deriva
    python3 auditoria_catalogo.py --warn    # nunca falla, solo reporta
"""
import json, os, subprocess, sys, urllib.request
import xml.etree.ElementTree as ET

FEED = ("https://inventory-feed.maxipublica.com/campaigns/xml/group/"
        "vehicle_feed_group_e1490ae1e92f.xml")
SITIO = "https://seminuevos.grupoplasencia.com/catalogo-piloto.json"
D1 = "crm-plasencia-db"
WORKER_DIR = os.path.expanduser("~/Documents/Grupo Plasencia/crm-worker")


def d1(sql):
    r = subprocess.run(["npx", "wrangler", "d1", "execute", D1, "--remote", "--json",
                        "--command", sql], capture_output=True, text=True, cwd=WORKER_DIR)
    return json.loads(r.stdout[r.stdout.index("["):])[0]["results"]


def get(url, binary=False):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read() if binary else json.loads(r.read())


def main():
    solo_warn = "--warn" in sys.argv
    print("=== Auditoria de paridad del catalogo ===")

    piloto = {str(x["agencia_id"]) for x in
              d1("SELECT DISTINCT agencia_id FROM inventario_seminuevos "
                 "WHERE piloto_otero=1 AND activo=1")}
    print(f"  sucursales del piloto: {len(piloto)}")

    # ── Maxipublica, acotado a nuestras sucursales ──
    root = ET.fromstring(get(FEED, binary=True))
    items = [e for e in root.iter() if e.tag.lower() in ("vehicle", "item", "ad", "listing")]
    feed, feed_total = {}, len(items)
    for it in items:
        aid = (it.findtext("dealer_id") or "").partition("-")[0]
        if aid in piloto:
            feed[(it.findtext("vehicle_id") or "").strip()] = (it.findtext("vin") or "").strip()

    # ── D1 y sitio ──
    d1r = {str(x["id_auto"]): (x["v"] or "") for x in
           d1("SELECT id_auto, COALESCE(NULLIF(TRIM(vin),''),'') v FROM inventario_seminuevos "
              "WHERE piloto_otero=1 AND activo=1")}
    web = {str(a["id"]): str(a.get("vin") or "").strip() for a in get(SITIO)}

    print(f"  feed (piloto) {len(feed)}  ·  D1 {len(d1r)}  ·  sitio {len(web)}"
          f"   [feed completo: {feed_total}]")

    fallos = 0
    for etiqueta, a, b in (("feed -> D1", feed, d1r), ("D1 -> sitio", d1r, web)):
        A, B = set(a), set(b)
        falta, sobra = A - B, B - A
        dif = [i for i in (A & B) if a[i].upper() != b[i].upper()]
        ok = not falta and not sobra and not dif
        fallos += 0 if ok else 1
        print(f"\n  {'OK ' if ok else 'XX '}{etiqueta}")
        print(f"      faltan {len(falta)} · sobran {len(sobra)} · VIN distinto {len(dif)}")
        for i in list(falta)[:5]:
            print(f"        falta  {i}  vin={a[i] or '(vacio)'}")
        for i in list(sobra)[:5]:
            print(f"        sobra  {i}  vin={b[i] or '(vacio)'}")
        for i in dif[:5]:
            print(f"        difiere {i}  origen={a[i]}  destino={b[i]}")

    # ── Cobertura de VIN: del ORIGEN, no defecto nuestro ──
    sin = [i for i, v in feed.items() if not v]
    pct = (len(feed) - len(sin)) * 100 // max(len(feed), 1)
    print(f"\n  -- cobertura de VIN en el piloto --")
    print(f"      {len(feed)-len(sin)}/{len(feed)} con VIN ({pct}%) · {len(sin)} sin VIN")
    print(f"      Falta en Maxipublica, no en nuestro pipeline. Ancla el Caso A")
    print(f"      de atribucion: sin VIN solo se atribuye por datos de la persona.")

    if fallos:
        print("\n  DERIVA DETECTADA. Casi siempre es lag del sync, no logica rota:")
        print("    el workflow declara cron horario pero GitHub Actions lo retrasa.")
        print("    Correr el sync y reauditar antes de buscar un bug:")
        print("      python3 parser_xml_maxipublica.py && python3 sync_via_worker.py \\")
        print("        && python3 export_catalogo_piloto.py")
    else:
        print("\n  Catalogo identico al feed en las sucursales del piloto.")

    sys.exit(0 if (solo_warn or not fallos) else 1)


if __name__ == "__main__":
    main()
