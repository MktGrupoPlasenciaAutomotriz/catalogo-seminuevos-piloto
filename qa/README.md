# QA de la landing en Chrome headless real

`test_card_gallery.js` prueba la galería de fotos de las tarjetas y de la ficha con touch
emulado (iPhone) y escritorio: swipe, flechas, teclado, apertura de la ficha en la foto
correcta, presencia del formulario y botón "Ver detalle y cotizar". No envía el formulario.

```bash
cd qa && npm init -y >/dev/null && npm i puppeteer-core@23 --no-audit --no-fund
python3 -m http.server 8001 --directory ../docs &      # servidor local
node test_card_gallery.js                               # contra localhost
QA_URL="https://seminuevos.grupoplasencia.com/?qa=1" node test_card_gallery.js   # contra producción
```

Usa el Chrome instalado en `/Applications/Google Chrome.app` (ajusta `CHROME` si cambia).
Deja `result.json` y capturas `card_*.png` en la carpeta. Los `node_modules` no se versionan.
