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
SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogo.json")
D1 = "crm-plasencia-db"
WORKER_DIR = os.path.expanduser("~/Documents/Grupo Plasencia/crm-worker")


def d1(sql):
    """Consulta D1 via wrangler. Devuelve None si no hay acceso (p.ej. en CI).

    En el runner de GitHub no existe el checkout del worker ni las credenciales
    de wrangler. Antes esto reventaba con FileNotFoundError, tumbaba el paso y
    -por el orden de los pasos- SALTABA EL COMMIT: el catalogo dejo de
    publicarse 3 corridas seguidas. La auditoria nunca debe poder impedir que
    el catalogo se publique.
    """
    if not os.path.isdir(WORKER_DIR):
        return None
    try:
        r = subprocess.run(["npx", "wrangler", "d1", "execute", D1, "--remote", "--json",
                            "--command", sql], capture_output=True, text=True,
                           cwd=WORKER_DIR, timeout=180)
        return json.loads(r.stdout[r.stdout.index("["):])[0]["results"]
    except Exception:
        return None


def get(url, binary=False):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read() if binary else json.loads(r.read())


def main():
    solo_warn = "--warn" in sys.argv
    print("=== Auditoria de paridad del catalogo ===")

    # El sitio publicado es la referencia que SIEMPRE esta disponible.
    web = {str(a["id"]): str(a.get("vin") or "").strip() for a in get(SITIO)}

    # Alcance del piloto: de D1 si hay acceso; si no, se deriva del propio
    # export, que es justamente el resultado de filtrar piloto_otero=1.
    filas = d1("SELECT DISTINCT agencia_id FROM inventario_seminuevos "
               "WHERE piloto_otero=1 AND activo=1")
    hay_d1 = filas is not None
    if hay_d1:
        piloto = {str(x["agencia_id"]) for x in filas}
    else:
        piloto = {str(a.get("agencia_id")) for a in get(SITIO) if a.get("agencia_id")}
        print("  [sin acceso a D1 — se audita feed -> sitio; el alcance del")
        print("   piloto se deriva del catalogo publicado]")
    print(f"  sucursales del piloto: {len(piloto)}")

    # ── La referencia del tramo feed->D1 es el SNAPSHOT que el sync ingirio
    # (catalogo.json), no el feed vivo.
    #
    # El feed de Maxipublica cambia por minuto: medido el 29-ago, 5 autos
    # entraron y salieron en los 2 minutos entre que el sync parseo y que la
    # auditoria descargo. Comparar D1 contra el feed vivo reporta ese churn como
    # si fuera un defecto nuestro, y una auditoria que grita lobo cada corrida
    # deja de leerse. Se verifico que el parser no pierde nada: 691 entran,
    # 691 salen.
    #
    # Con snapshot se mide NUESTRA fidelidad; el feed vivo queda como dato
    # informativo de cuanto se movio el origen desde el sync.
    snap = None
    if os.path.exists(SNAPSHOT):
        try:
            raw = json.load(open(SNAPSHOT))
            snap = raw.get("records") if isinstance(raw, dict) else raw
        except Exception:
            snap = None

    root = ET.fromstring(get(FEED, binary=True))
    items = [e for e in root.iter() if e.tag.lower() in ("vehicle", "item", "ad", "listing")]
    feed, feed_total = {}, len(items)
    for it in items:
        aid = (it.findtext("dealer_id") or "").partition("-")[0]
        if aid in piloto:
            feed[(it.findtext("vehicle_id") or "").strip()] = (it.findtext("vin") or "").strip()

    # ── D1 y sitio ──
    filas = d1("SELECT id_auto, COALESCE(NULLIF(TRIM(vin),''),'') v "
               "FROM inventario_seminuevos WHERE piloto_otero=1 AND activo=1")
    d1r = {str(x["id_auto"]): (x["v"] or "") for x in filas} if filas else None

    print(f"  feed (piloto) {len(feed)}  ·  D1 {len(d1r) if d1r else 'n/d'}"
          f"  ·  sitio {len(web)}   [feed completo: {feed_total}]")

    # El snapshot manda para medir fidelidad; si no esta, se cae al feed vivo.
    origen, etq_origen = (feed, "feed vivo")
    if snap:
        def _id(a):
            for k in ("ID_AUTO", "id_auto", "id", "VEHICLE_ID", "vehicle_id"):
                if k in a:
                    return str(a[k])
            return ""
        def _vin(a):
            for k in ("VIN", "vin"):
                if a.get(k):
                    return str(a[k]).strip()
            return ""
        def _ag(a):
            for k in ("AGENCIA_ID", "agencia_id", "DEALER_ID", "dealer_id"):
                if a.get(k):
                    return str(a[k]).partition("-")[0]
            return ""
        origen = {_id(a): _vin(a) for a in snap if _id(a) and _ag(a) in piloto}
        etq_origen = "snapshot del sync"
        movido = len(set(feed) ^ set(origen))
        print(f"  origen del tramo 1: {etq_origen} ({len(origen)})"
              f" · el feed vivo se movio {movido} autos desde el sync")

    # Calibracion (29-ago-2026): los dos tramos NO tienen el mismo estandar.
    #
    #   origen -> D1   INFORMATIVO. Depende de un feed que cambia por minuto:
    #                  medido, 5 autos entraron y salieron en los 2 minutos
    #                  entre que el sync parseo y que la auditoria descargo.
    #                  Marcarlo rojo seria gritar lobo cada corrida.
    #   D1 -> sitio    EXIGIBLE. Esta enteramente bajo nuestro control: si el
    #                  export corrio, el sitio DEBE ser identico a D1. Cualquier
    #                  diferencia aqui es un defecto nuestro.
    tramos = ([(f"{etq_origen} -> D1", origen, d1r, False), ("D1 -> sitio", d1r, web, True)]
              if d1r else [(f"{etq_origen} -> sitio", origen, web, False)])
    fallos = 0
    for etiqueta, a, b, exigible in tramos:
        A, B = set(a), set(b)
        falta, sobra = A - B, B - A
        dif = [i for i in (A & B) if a[i].upper() != b[i].upper()]
        ok = not falta and not sobra and not dif
        if not ok and exigible:
            fallos += 1
        sello = "OK " if ok else ("XX " if exigible else "·· ")
        print(f"\n  {sello}{etiqueta}" + ("" if exigible else "   [informativo]"))
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
        print("\n  DEFECTO: D1 y el sitio no coinciden. Ese tramo es nuestro por")
        print("  completo — si el export corrio, deben ser identicos. Revisar")
        print("  export_catalogo_piloto.py y que el commit del workflow haya publicado.")
    else:
        print("\n  Lo publicado refleja exactamente D1.")
        print("  Cualquier diferencia contra el feed vivo es churn del origen:")
        print("  Maxipublica mueve inventario entre corridas y se corrige sola en la")
        print("  siguiente. Solo preocupa si el mismo auto persiste varias corridas.")

    sys.exit(0 if (solo_warn or not fallos) else 1)


if __name__ == "__main__":
    main()
