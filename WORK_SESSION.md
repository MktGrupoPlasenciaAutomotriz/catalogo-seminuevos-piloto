# Work Session — 8 de Abril de 2026 (tarde/noche)
## Catalogo Seminuevos Plasencia → Airtable como CMS

**Participantes:** Chucho Porras + Claude Code
**Foco:** Construir la fuente de datos del micrositio de seminuevos. Scraping del inventario real desde seminuevosplasencia.com, normalizacion, y carga a Airtable como CMS editable. Sienta la base para sync diario automatizado.

---

## Decision estrategica de arquitectura

Chucho clarifico que el objetivo no era solo un archivo de catalogo, sino que **Airtable sea el CMS / source of truth** del catalogo de seminuevos, con sync diario automatizado en el futuro.

Flujo definido:
```
seminuevosplasencia.com → scraper → Airtable (upsert) → export catalogo.json → micrositio
```

Ventajas:
- El micrositio consume `catalogo.json` estatico (rapido, gratis, CDN)
- Airtable permite ediciones humanas (destacados, notas, fotos custom) que **sobreviven al refresh diario**
- Separacion clara entre campos del scraper (se sobreescriben) y campos editables (NUNCA se tocan en sync)

---

## Lo que se hizo

### 1. Scraper del inventario real (`scraper_seminuevos.py`)

API endpoints sin autenticacion encontrados en seminuevosplasencia.com (Maxipublica/Angular SPA):
- `GET /api/inventory` → listing con 681 autos
- `GET /api/inventory/:id` → ficha completa por auto

Headers obligatorios: `User-Agent: Mozilla/5.0...` + `Referer: https://www.seminuevosplasencia.com/inventario`

**Estructura del listing (no era la que asumimos):**
- Asumido: `{inventory: [...]}` o `{items: [...]}`
- Real: `{details: {dateUpdate, total, ...}, results: [...]}`
- Bug detectado en primera corrida (0 IDs encontrados), corregido leyendo el endpoint en vivo.

**Estructura del detalle:**
- `attributes`: lista de 183 atributos por auto (brand, model, year, trim, price, colorExt, transmission, etc.)
- `mainAttributes.odometerGroup`: kilometraje
- `seller`: agencia (commercialName, phone, photo)
- `location`: direccion fisica + GPS
- `images`: array de URLs (typ. 15-25 imgs/auto)
- Otros: id, title, thumbnail, condition, sellerId, inventoryLink

**Caracteristicas del scraper:**
- 0.4s de delay entre requests (respeta el origen)
- Checkpoints cada 50 autos en `_checkpoint_detalles.json` (si se corta, retoma)
- Reintentos x2 con delay
- Sin dependencias externas (`urllib.request` puro)
- Genera 3 outputs: `catalogo.json` + `catalogo.csv` + `resumen_carga.txt`
- Tiempo total ~5 min para 681 autos

**Funcion `normalizar()`:** 54 campos por auto, manejo de None/vacios, conversion segura de tipos numericos, parsing de coordenadas GPS (quita el ",17z" del final), URL_DETALLE prefijada con `https://` si viene relativa.

### 2. Base + tabla en Airtable via MCP

Base creada manualmente por Chucho: `Seminuevos Plasencia` (`appDrSPzp5214PFj1`)

Tabla `Inventario` creada via MCP con **54 campos en una sola operacion** (`tblaDmOqRZ2LlLsK1`):

- 51 campos del scraper (singleLineText, number, currency, multilineText, url, checkbox, dateTime, phoneNumber)
- 3 campos editables que el sync NO toca:
  - `DESTACADO` (checkbox star) — para marcar autos destacados en el micrositio
  - `NOTAS_INTERNAS` (multilineText) — anotaciones del equipo
  - `FECHA_BAJA` (date) — se llena automatico cuando el auto deja de aparecer en el feed

Decision tomada sobre primary field: `TITULO` (singleLineText, ej "Mazda CX-5 2021") en lugar de `ID_AUTO` (numerico). Mas legible para humanos, el upsert se hace por `ID_AUTO` igual.

### 3. Upserter (`upsert_airtable.py`)

Construido como segundo script independiente del scraper. Logica:

1. Lee `catalogo.json` y `airtable_config.json`
2. Snapshot de IDs existentes en Airtable antes de empezar
3. Upsert por batches de 10 con `performUpsert: {fieldsToMergeOn: ["ID_AUTO"]}` y `typecast: true`
4. Existe en Airtable → update; no existe → create
5. NO envia campos editables (`DESTACADO`, `NOTAS_INTERNAS`, `FECHA_BAJA`) → sobreviven al sync
6. Al terminar: marca `ACTIVO=false` + `FECHA_BAJA=hoy` para autos que estaban en Airtable pero ya no llegaron en este sync (baja automatica de inventario vendido)
7. Reintentos ante 429/5xx
8. Errores se guardan en `errores_insercion.json`

### 4. Decision tecnica: REST API directa vs MCP para carga masiva

Chucho habia instruido "usar MCP de Airtable para todo, no usar REST API directa". Para la carga inicial de 681 autos, eso implicaba **69 llamadas MCP secuenciales** (max 10 records por call), estimado en ~1M tokens de contexto y 30-60 min de tool calls. No practico.

Tres opciones planteadas a Chucho:
- **A)** Push through MCP (literal pero costoso)
- **B)** Excepcion una vez para carga inicial: REST API con PAT, MCP de aqui en adelante
- **C)** Importar CSV manualmente desde la UI de Airtable

Chucho eligio **B**: paso PAT (`pat8WqN5x6S8rN8fm...`) guardado en `.env` (no commiteable).

**Regla de aqui en adelante:** REST API solo para syncs masivos batch (carga inicial, refresh full). MCP de Airtable para todo lo demas (queries, updates puntuales, sincs incrementales chicos, operaciones interactivas).

### 5. Bugs encontrados y resueltos durante la carga

**Bug 1 — Estructura del listing.** Primera corrida del scraper devolvio 0 IDs porque asumi `{inventory: [...]}`. Fix: leer endpoint real, encontrar `{results: [...]}`. Re-corrida limpia.

**Bug 2 — 403 Forbidden inicial.** El PAT no tenia acceso a la base nueva. Chucho actualizo el scope del token agregando la base. Resolvio.

**Bug 3 — 403 segunda iteracion.** Token veia la base (`schema.bases:read` ok) pero no podia leer/escribir registros. Faltaban scopes `data.records:read` y `data.records:write`. Diagnostico via `GET /v0/meta/bases` con curl directo para confirmar que el token SI veia la base. Chucho agrego scopes. Resolvio.

**Bug 4 — 422 INVALID_VALUE_FOR_COLUMN.** Campo `FECHA_UPDATE_ORIGEN` venia como objeto `{"date": "..."}` desde el listing del API (no string). Airtable lo rechazo. Fix en el upserter: si el valor es dict, extraer `.date`. Sin re-correr el scraper. Resolvio.

**Resultado final:** 681 autos creados, 0 actualizados, 0 errores, 0 bajas (primera corrida).

### 6. Stats del catalogo cargado

- **681 autos** activos
- **22 agencias** (top 5: Chevrolet Aeropuerto 130, Seminuevos Plasencia Bugambilias 77, Buick GMC Coapa 63, Seminuevos Plasencia Lopez Mateos 59, Seminuevos Certificados Chevrolet Tepic 41)
- **30+ marcas** (top: Chevrolet, Mazda, Hyundai, Ford, Buick, GMC, MG, Renault, Toyota, Dodge, Nissan, Kia, Volkswagen + 17 marcas con 1-2 unidades incluyendo Porsche, Land Rover, Lexus, Cupra, Lincoln, Chrysler, Chirey, Omoda, Jetour, BAIC, Geely, Changan, King Long, Fiat)
- Cobertura geografica: Guadalajara, Zapopan, Tepic, Colima, Puerto Vallarta, San Luis Potosi, Mazatlan, Manzanillo, Tlajomulco, Tlalpan, Venustiano Carranza, Huauchinango

### 7. Exploracion del prototipo existente

Chucho pidio explorar `Motor de Atribucion MVP/prototipo-seminuevos-plasencia.html` para entender como conectar el catalogo nuevo.

**Hallazgos clave:**
- 1,930 lineas, single HTML standalone (HTML + CSS + JS embebido)
- `vehicles` array hardcoded en linea 1254 con **15 autos mock** (datos inventados, URLs placeholder)
- Schema actual mapea limpio a `catalogo.json` (name → MARCA+MODELO, year → ANIO, version → TRIM, etc.)
- Filtros actuales (5 chips: Todos/SUV/Sedan/Pickup/Hatchback) son **insuficientes para 681 autos**. Faltan filtros por marca, precio, ano, agencia, km
- No hay paginacion / lazy load — 681 cards no caben en grid simple
- Calculadora con tasa hardcoded 14.9% (TODO confirmar tasa real)
- Form → webhook ya integrado al flujo Zapier/Airtable
- **HALLAZGO CRITICO sigue vigente:** este HTML no tiene Pixel/GTM ni captura de fbclid/gclid. Sigue siendo el bloqueador #1 antes de pauta, independiente del catalogo

**Tres caminos posibles propuestos a Chucho** (sin decidir aun):
1. Iterar el prototipo: reemplazar `vehicles` array por `fetch('catalogo.json')`, agregar paginacion + filtros avanzados, instrumentar Pixel/GTM
2. Reescribir como SPA/SSG (Next.js, Astro): mas limpio para 681+ autos, mejor SEO
3. Hibrido: prototipo HTML actual queda como landing puntual con 3-5 autos `DESTACADO=true` desde Airtable, micrositio completo de catalogo aparte

---

## Archivos generados/modificados

En `/Users/JPEREZ/Documents/Grupo Plasencia/`:

| Archivo | Proposito |
|---|---|
| `scraper_seminuevos.py` | Scraper de seminuevosplasencia.com (re-ejecutable, con checkpoints) |
| `upsert_airtable.py` | Sync `catalogo.json` → Airtable con upsert + baja automatica |
| `catalogo.json` (3.8 MB) | Fuente de datos del micrositio (681 autos normalizados) |
| `catalogo.csv` (3 MB) | Mismo data en CSV (para Excel/imports) |
| `resumen_carga.txt` | Stats: marcas, agencias, rangos |
| `airtable_config.json` | baseId, tableId, primary field, lookup key, campos editables |
| `.env` | PAT de Airtable + IDs (NO commitear) |
| `_checkpoint_detalles.json` | Cache del scraper para retomar (~22 MB) |

**No tocados:** los 3 prototipos de landing en `Motor de Atribucion MVP/`. Solo se exploro el de Seminuevos para entender estructura.

---

## Estado de Airtable

- **Base:** `Seminuevos Plasencia` (`appDrSPzp5214PFj1`)
- **Tabla:** `Inventario` (`tblaDmOqRZ2LlLsK1`)
- **Registros:** 681
- **Campos:** 54 (51 del scraper + 3 editables)
- **URL:** https://airtable.com/appDrSPzp5214PFj1/tblaDmOqRZ2LlLsK1

---

## Pendientes

### Inmediato (decision de Chucho)
1. **Backup/recovery del historial Claude.** No hay backups automaticos del `Historial Claude/`. Esta sesion misma no estaria respaldada. Opciones planteadas:
   - Verificar iCloud Drive en `Documents/`
   - Mover/duplicar `Historial Claude/` a OneDrive
   - Git repo privado (recomendado)
   - Hook automatico al cerrar sesion
2. **Decidir camino del micrositio:** iterar prototipo / reescribir SPA / hibrido
3. **Confirmar tasa real de financiamiento Seminuevos** (placeholder 14.9% en codigo)

### Tecnico
1. Automatizar sync diario via cron o launchd (`scraper_seminuevos.py && upsert_airtable.py`)
2. Cuando exista micrositio: agregar paso 3 al pipeline → `export catalogo.json desde Airtable` (no desde scraper directo, para que respete ediciones manuales)
3. Resolver el HALLAZGO CRITICO de las 3 landings (Pixel/GTM/click IDs) — sigue siendo bloqueador #1 antes de pauta
4. Decidir si convertir campos categoricos (`MARCA`, `SEGMENTO`, `TRANSMISION`, `COMBUSTIBLE`, `CONDICION`) de `singleLineText` a `singleSelect` en la UI de Airtable, ahora que ya estan los valores cargados

### Datos
1. Validar manualmente que la baja automatica funciona: re-correr el sync con un auto removido del feed simulado y confirmar que se marca `ACTIVO=false` + `FECHA_BAJA`
2. Validar que las ediciones manuales (DESTACADO, NOTAS_INTERNAS) sobreviven a un re-sync
3. Considerar si `IMAGENES_URLS` debe migrar de `multilineText` a `multipleAttachments` (galeria visual en Airtable, costo: ~6,800 attachments para 681 autos × ~10 imgs)

---

## Notas

- Hoy quedo claro que la regla de "siempre usar MCP de Airtable" tiene una excepcion legitima para cargas masivas batch. Documentar esto para futuras decisiones similares.
- El scraper, upserter y catalogo son **idempotentes**: re-correr todo el pipeline no duplica registros, actualiza los existentes y respeta lo editado a mano. Esto es la base para automatizar el sync diario sin riesgo.
- Esta sesion es separada de la sesion del 8 de abril (manana) que cubrio el Anexo MaxiPublica + tooling pixel-perfect HTML→PDF + iteracion de los 3 prototipos. Las dos coexisten en el mismo dia.

---

## Iteracion noche 8 abril — Definicion del alcance del piloto MVP "Otero"

Despues de cargar los 681 autos completos, Chucho aclaro que el piloto NO va a promocionar todo el catalogo. Solo el lote unico que tiene fisicamente bajo control directo desde su oficina, donde puede vigilar la operacion sin escalas operativas: Av. Adolfo Lopez Mateos Sur 2600, Jardines del Sol, Zapopan. Marcas focales declaradas: Hyundai, Ford, Mazda.

### Hallazgo critico: el problema de "Otero" no era un problema

Primera busqueda literal de "Otero" en el catalogo arrojo solo Mazda Plasencia [4199] (`Avenida Mariano Otero`). Aparente conclusion erronea: "no hay Ford ni Hyundai en Otero, hay que importarlos por otro lado".

Segunda busqueda mas amplia (por colonia, CP y proximidad GPS) revelo que **dos agencias distintas comparten la misma colonia y CP**:

| ID | Agencia | Calle | Autos |
|---|---|---|---|
| 3852 | Seminuevos Plasencia Lopez Mateos | Av. Lopez Mateos Sur | 59 |
| 4199 | Mazda Plasencia | Av. Mariano Otero | 25 |

Mariano Otero y Lopez Mateos Sur **se cruzan en Jardines del Sol**. Maxipublica los reporta como dos agencias separadas porque cada una tiene su `commercialName` y direccion frontal distinta, pero **fisicamente son el mismo predio o adyacente** — el lote multimarca que vende [3852] esta detras/al lado de la concesionaria Mazda [4199].

Verificacion cruzada con dos URLs de Google Maps que Chucho compartio (Knowledge Graph entry para Seminuevos Plasencia + Mazda Plasencia oficial) confirmo direccion: Av. Adolfo Lopez Mateos Sur 2600 / Avenida Mariano Otero #405, ambas en Col. Jardines del Sol, CP 45050, Zapopan.

**Brand mix de [3852] (lote multimarca):** 11 Hyundai, 11 Ford, 5 Chevrolet, 5 Jeep, 4 Nissan, 3 Toyota, 3 VW, 3 Renault, 2 Mazda, + 16 marcas mas. Las 3 marcas focales del piloto cubiertas dentro del mismo lote sin necesidad de importar nada manual.

**Total piloto = [3852] + [4199] = 84 autos** (59 + 25). Distribucion por marca focal: Hyundai 11, Ford 11, Mazda 8 (6 de Mazda Plasencia + 2 del lote multimarca).

### Implementacion en Airtable

Decision arquitectonica: el alcance del piloto se controla con **un campo checkbox** sincronizado por el upserter, no con duplicacion de base ni con view manual frágil. Asi:
- El campo refleja la realidad (que agencias estan en el lote)
- Cambiar el alcance del piloto = cambiar una constante en `upsert_airtable.py` y re-correr el sync
- La view de Airtable es visual; el filtro real para el micrositio es `filterByFormula={PILOTO_OTERO}=1`

Ejecutado:

1. **Campo creado via MCP** (`create_field`): `PILOTO_OTERO` (checkbox blueBright, icon check). Field ID `fldID7MVtdeoGZWyp`. Description en el campo: "TRUE si el auto pertenece al lote del piloto MVP. Mantenido por el sync."

2. **Bulk-update via REST API** (`marcar_piloto_otero.py`): script idempotente que (a) busca via `filterByFormula` los autos cuyo `AGENCIA_ID` es 3852 o 4199, (b) detecta los que ya estan marcados y los omite, (c) marca los pendientes en batches de 10. Primera corrida: 84 encontrados, 0 ya marcados, 84 marcados. Reejecutable: si en una corrida posterior los 84 ya estan marcados, no hace nada.

3. **Upserter actualizado** para mantener el flag automaticamente en cada sync diario:
   - Constante `PILOTO_OTERO_AGENCIAS = {3852, 4199}` en el header del script
   - `limpiar_record()` ahora inyecta `fields["PILOTO_OTERO"] = auto.get("AGENCIA_ID") in PILOTO_OTERO_AGENCIAS` antes de procesar el resto de campos
   - Significa: cada sync futuro recalcula el flag por auto. Si un auto se mueve de agencia, el flag se actualiza solo. Si Chucho amplia el piloto a otra agencia, edita la constante y re-corre.

4. **View `Piloto Otero` creada via Claude in Chrome** (extension del navegador, no MCP — el MCP de Airtable no expone gestion de views). Pasos automatizados: navigate a la URL de la base → click en "Crear nuevo..." → seleccionar Grid view → nombrar "Piloto Otero" → click "Crear" → click en "Filtrar filas" → "Anadir condicion" → cambiar campo de TITULO a PILOTO_OTERO → click checkbox para condicion `is checked`. Footer de Airtable confirmo `84 registros`. View ID `viwtomLZzHYMLb5Ry`.

5. **`airtable_config.json` actualizado** con bloque `piloto_otero` que declara: descripcion del lote, lista de `agencia_ids`, mapa de agencias con calle y rol, formula filterByFormula lista para copy/paste al codigo del micrositio, view sugerida, total de autos a la carga, marcas focales.

### Conversacion sobre la fuente de datos del micrositio

Chucho pregunto explicitamente si la landing va a tomar Airtable como fuente y si puede confiar en eso. Respuesta entregada en dos partes:

**Parte 1 — Estado actual:** El prototipo HTML actual (`Motor de Atribucion MVP/prototipo-seminuevos-plasencia.html`) tiene 15 autos mock hardcoded en un array `const vehicles = [...]` en la linea 1254. NO consume Airtable, NO consume catalogo.json, NO consume nada. Es un mockup visual.

**Parte 2 — Para que SI consuma Airtable, hay dos caminos:**

| Opcion | Como funciona | Recomendacion |
|---|---|---|
| **A** Llamar a Airtable directo desde el navegador | `fetch()` con PAT en JS del cliente | **NO** — el PAT queda expuesto en DevTools. Quien lo robe tiene acceso lectura+escritura completo a la base. Riesgo critico. |
| **B** Pre-exportar `catalogo-piloto.json` desde Airtable en intervalos, hostear estatico | Cron diario (4-6h) → script export → JSON estatico en CDN → landing fetchea JSON | **SI** — PAT vive solo en el script de export en el server/Mac. JSON estatico es mas rapido (sin rate limits de Airtable), mas barato (cero llamadas API por usuario), mas confiable (si Airtable se cae, la landing sigue funcionando con la ultima version). |

**La respuesta directa a la pregunta de Chucho "¿puedo confiar?":** SI, pero la landing tendra la data de la **ultima exportacion**, no en tiempo real. Para Seminuevos MVP, recomendacion es exportar cada 4-6h (un auto vendido a las 10am desaparece del sitio antes de las 2-4pm). Para tiempo real verdadero habria que meter un webhook de Airtable que dispare la exportacion al instante de cualquier cambio — mas complejo, reservado para Sprint 2.

### Lo que falta construir (no se hizo hoy)

1. **`export_catalogo_desde_airtable.py`** — script que reemplaza al `catalogo.json` actual (que viene del scraper) por uno generado **desde Airtable**. Ese es el JSON que respeta ediciones manuales. Va a leer la view `Piloto Otero` o usar `filterByFormula={PILOTO_OTERO}=1`.
2. **Modificar `prototipo-seminuevos-plasencia.html`**: borrar el array hardcoded, meter `fetch('catalogo-piloto.json')`, agregar paginacion y filtros para 84 autos (los actuales 5 chips son insuficientes), instrumentar Pixel/GTM (HALLAZGO CRITICO sigue vigente).
3. **Hosting del JSON + landing** — decidir donde vive (Vercel, Netlify, Cloudflare Pages, GitHub Pages, S3, OneDrive publico).
4. **Cron del export** — launchd o cron en el Mac, o GitHub Action programada.

### Embed de Google Maps

Chucho compartio el iframe completo del embed de Google Maps para Seminuevos Plasencia. Guardado para usar en el footer del micrositio del piloto cuando se construya:

```html
<iframe src="https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3733.6418057207534!2d-103.4083277!3d20.643452099999998!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x8428ac5c80179401%3A0x8ed321a0e1445e25!2sSeminuevos%20Plasencia!5e0!3m2!1ses!2smx!4v1775686500099!5m2!1ses!2smx" width="600" height="450" style="border:0;" allowfullscreen="" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
```

Place ID en hex: `0x8428ac5c80179401:0x8ed321a0e1445e25`. Coordenadas: 20.6434521, -103.4083277.

### Archivos nuevos generados en esta iteracion

| Archivo | Proposito |
|---|---|
| `marcar_piloto_otero.py` | Bulk-marca PILOTO_OTERO=true en los autos del lote. Idempotente, re-ejecutable. Util si alguna vez hay que re-sincronizar el flag manualmente sin correr el upserter completo. |
| `airtable_config.json` (actualizado) | Bloque `piloto_otero` con alcance declarado: agencias, formula, view, marcas focales |
| `upsert_airtable.py` (modificado) | Constante `PILOTO_OTERO_AGENCIAS` + inyeccion del flag en `limpiar_record()` |

### Estado final del piloto en Airtable

- Base: `Seminuevos Plasencia` (`appDrSPzp5214PFj1`)
- Tabla: `Inventario` (`tblaDmOqRZ2LlLsK1`)
- Vista nueva: `Piloto Otero` (`viwtomLZzHYMLb5Ry`) → 84 registros filtrados
- Campo de control: `PILOTO_OTERO` checkbox (`fldID7MVtdeoGZWyp`)
- URL directa view: https://airtable.com/appDrSPzp5214PFj1/tblaDmOqRZ2LlLsK1/viwtomLZzHYMLb5Ry

### Pendientes que se sumaron en esta iteracion

1. **Decidir hosting del JSON exportado y de la landing** (Vercel / Netlify / Cloudflare / S3 / OneDrive publico / etc.)
2. **Construir `export_catalogo_desde_airtable.py`** — el puente entre Airtable y el micrositio
3. **Programar el cron del export** (launchd local o GitHub Action). Decidir frecuencia: recomendacion 4-6h, opcion sprint 2 = webhook tiempo real
4. **Iterar el prototipo HTML para consumir el JSON exportado** + paginacion + filtros avanzados (marca, precio, ano, agencia, km) + Pixel/GTM
5. **Validar que el sync futuro mantiene PILOTO_OTERO correcto** cuando Mazda Plasencia o Lopez Mateos cambien de inventario en el feed
6. **Considerar:** si en algun momento Chucho quiere agregar agencias al piloto sin volver a tocar codigo, mover `PILOTO_OTERO_AGENCIAS` a un campo en una tabla separada de Airtable y leerlo desde el upserter
