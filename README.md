# Catalogo Seminuevos Plasencia — Piloto Otero MVP

Pipeline de sincronizacion del catalogo de seminuevos de Grupo Plasencia.

```
seminuevosplasencia.com  →  scraper  →  Airtable (CMS)  →  micrositio
```

## Que hace

1. **scraper_seminuevos.py** — extrae los ~681 autos del feed publico de seminuevosplasencia.com (Maxipublica API). Sin auth, con headers obligatorios. Genera `catalogo.json` y `catalogo.csv`.
2. **upsert_airtable.py** — sincroniza `catalogo.json` a una base de Airtable. Hace upsert por `ID_AUTO` (no duplica), respeta campos editables a mano (`DESTACADO`, `NOTAS_INTERNAS`), marca `ACTIVO=false` + `FECHA_BAJA` a los autos que ya no llegan en el feed (baja automatica de inventario vendido). Mantiene el flag `PILOTO_OTERO` sincronizado segun `AGENCIA_ID`.
3. **marcar_piloto_otero.py** — script utilitario para marcar/refrescar el flag PILOTO_OTERO sin correr el upserter completo. Idempotente.

## Alcance del piloto MVP

Lote unico bajo control directo desde la oficina:
**Av. Adolfo Lopez Mateos Sur 2600, Jardines del Sol, 45050 Zapopan, Jal.**

Agencias incluidas:
- `[3852]` Seminuevos Plasencia Lopez Mateos (lote multimarca, 59 autos)
- `[4199]` Mazda Plasencia (concesionaria Mazda contigua, 25 autos)

**Total: 84 autos.** Marcas focales del piloto: Hyundai (11), Ford (11), Mazda (8). Mas otras 17 marcas menores.

## Airtable

- **Base:** `Seminuevos Plasencia` (`appDrSPzp5214PFj1`)
- **Tabla:** `Inventario` (`tblaDmOqRZ2LlLsK1`)
- **Vista del piloto:** `Piloto Otero` (`viwtomLZzHYMLb5Ry`) — filtrada por `PILOTO_OTERO is checked`
- **Campos editables a mano (NUNCA tocados por sync):** `DESTACADO`, `NOTAS_INTERNAS`, `FECHA_BAJA`

## Variables de entorno requeridas

Archivo `.env` (NUNCA commitear, esta en `.gitignore`):

```
AIRTABLE_PAT=patXXXXXXXXXXXXXXX.YYYY...
AIRTABLE_BASE_ID=appDrSPzp5214PFj1
AIRTABLE_TABLE_ID=tblaDmOqRZ2LlLsK1
```

En GitHub Actions estos viven como **Secrets** (Settings → Secrets and variables → Actions).

## Uso local

```bash
python3 scraper_seminuevos.py     # ~5 min, genera catalogo.json
python3 upsert_airtable.py        # ~30s, sync a Airtable
```

Ambos scripts son **idempotentes**: re-correrlos no duplica nada, actualiza lo existente, respeta ediciones manuales.

## Sync automatico (GitHub Actions)

Ver `.github/workflows/sync-diario.yml`. Corre todos los dias a las 04:03 hora Guadalajara (10:03 UTC). Independiente de que la Mac local este prendida.

Para correr el sync manualmente sin esperar al cron: pestaña Actions del repo → "Sync diario catalogo Seminuevos" → "Run workflow".

## Filosofia tecnica

- **Idempotencia.** Cualquier script puede correrse 1 o 100 veces seguidas sin efectos secundarios.
- **Source of truth en Airtable.** El JSON estatico que consume el micrositio se exporta DESDE Airtable, no DESDE el scraper directo. Asi las ediciones manuales sobreviven al refresh diario.
- **Separacion de campos del scraper vs editables.** Los campos del scraper se sobreescriben en cada sync. Los campos editables (`DESTACADO`, `NOTAS_INTERNAS`) jamas se tocan en codigo.
- **PAT nunca en frontend.** El Personal Access Token de Airtable vive solo en `.env` local y en GitHub Secrets. El navegador del usuario nunca lo ve.
