// Prueba funcional + visual de la galería en tarjetas en Chrome headless real.
// Cada gesto re-localiza la tarjeta (las capturas con clip mueven el scroll).
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const URL = process.env.QA_URL || 'http://localhost:8001/?qa=1';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const out = {};

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/CORS|ERR_FAILED|sedes/.test(m.text())) errors.push('console: ' + m.text().slice(0, 160)); });

  const locate = async (sel, part) => {
    await page.evaluate(s => document.querySelector(s).scrollIntoView({ block: 'center', behavior: 'instant' }), sel);
    await sleep(350);
    return (await page.$(sel + (part === undefined ? ' .v-card-img-wrap' : part))).boundingBox();
  };
  const shot = async (name, sel, part) => {
    await locate(sel, part);
    const h = await page.$(sel + (part === undefined ? ' .v-card-img-wrap' : part));
    try { await h.screenshot({ path: name }); } catch (e) { console.error('shot', name, e.message.slice(0, 100)); }
  };
  const state = (sel) => page.evaluate(sel => {
    const c = document.querySelector(sel), g = c.querySelector('.v-card-gal'), t = g.querySelector('.v-card-track');
    const slides = [...c.querySelectorAll('.v-card-slide')];
    const img = i => slides[i] && slides[i].querySelector('img');
    return { n: g.dataset.n, idx: g.dataset.idx, hydrated: g.dataset.hydrated || null, scrollIdx: Math.round(t.scrollLeft / Math.max(1, t.clientWidth)),
      imgs: c.querySelectorAll('.v-card-slide img').length, counter: c.querySelector('[data-gal-count]').textContent,
      second: img(1) ? img(1).loading : null, third: img(2) ? img(2).loading : null, secondLoaded: !!(img(1) && img(1).complete && img(1).naturalWidth > 0),
      activeDot: [...g.querySelectorAll('.v-card-dots i')].findIndex(d => d.classList.contains('on')),
      navPrev: getComputedStyle(g.querySelector('.v-card-nav.prev')).opacity, navNext: getComputedStyle(g.querySelector('.v-card-nav.next')).opacity,
      prevDisabled: g.querySelector('.v-card-nav.prev').getAttribute('aria-disabled'),
      events: (window.dataLayer || []).filter(e => e.event === 'card_gallery_nav').map(e => e.method), modalOpen: document.body.classList.contains('gp-modal-open') };
  }, sel);
  const swipe = async (b, dir) => {   // dir -1 = siguiente (dedo a la izquierda)
    const y = b.y + b.height / 2, x0 = b.x + b.width * (dir < 0 ? 0.85 : 0.15);
    await page.touchscreen.touchStart(x0, y);
    for (let i = 1; i <= 12; i++) { await page.touchscreen.touchMove(x0 + dir * b.width * 0.06 * i, y); await sleep(30); }
    await page.touchscreen.touchEnd();
    await sleep(1000);
  };

  // ── MÓVIL (iPhone, touch) ──
  await page.emulate({ viewport: { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true }, userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1' });
  await page.goto(URL, { waitUntil: 'networkidle2', timeout: 60000 });
  await page.waitForSelector('.v-card .v-card-gal', { timeout: 20000 });
  await sleep(500);
  const sel = await page.evaluate(() => { const c = [...document.querySelectorAll('.v-card')].find(c => +c.querySelector('.v-card-gal').dataset.n > 1); c.id = 'qaCard'; return '#qaCard'; });
  out.m1_initial = await state(sel);
  out.m0_ux = await page.evaluate(sel => { const c = document.querySelector(sel); const fs = el => el ? getComputedStyle(el).fontSize : null; return { viewport: document.querySelector('meta[name=viewport]').content, chipMarca: fs(c.querySelector('.v-card-body span')), disclaimer: fs(c.querySelector('.v-card-price-disclaimer')), disclaimerColor: getComputedStyle(c.querySelector('.v-card-price-disclaimer')).color, formInput: fs(document.querySelector('.form-input')) }; }, sel);
  await shot('card_mobile_1.png', sel);
  await shot('card_mobile_full.png', sel, '');

  await swipe(await locate(sel), -1);
  out.m2_after_swipe = await state(sel);
  await shot('card_mobile_2.png', sel);

  await swipe(await locate(sel), -1);
  out.m3_after_second_swipe = await state(sel);

  await swipe(await locate(sel), +1);
  out.m4_after_back_swipe = await state(sel);

  const nb = await locate(sel, ' .v-card-nav.next');
  await page.touchscreen.tap(nb.x + nb.width / 2, nb.y + nb.height / 2); await sleep(900);
  out.m5_after_arrow_tap = Object.assign(await state(sel), { navHit: nb.width + 'x' + nb.height });

  const ib = await locate(sel);
  await page.touchscreen.tap(ib.x + ib.width / 2, ib.y + ib.height / 2); await sleep(700);
  out.m6_tap_opens_modal = await page.evaluate(sel => {
    const c = document.querySelector(sel), g = c.querySelector('.v-card-gal');
    return { modalOpen: document.body.classList.contains('gp-modal-open'), galleryIdx: typeof galleryIdx !== 'undefined' ? galleryIdx : null, cardIdx: g.dataset.idx,
      modalCount: document.getElementById('modalPhotoCount').textContent, srcMatches: document.getElementById('modalImg').src === c.querySelectorAll('.v-card-slide img')[+g.dataset.idx].src };
  }, sel);
  out.m6b_form_in_modal = await page.evaluate(() => { const f = document.getElementById('plasiForm'); return { form: !!f, inputs: f ? f.querySelectorAll('input').length : 0, submitBtn: !!(f && (f.querySelector('button[type=submit], .btn-conversion, button.btn-conversion'))), submitFn: typeof submitForm === 'function' || typeof handleSubmit === 'function' || 'unknown' }; });
  // Swipe en la galería de la ficha
  const mg = await (await page.$('#modalGallery')).boundingBox();
  const before = await page.evaluate(() => galleryIdx);
  await page.touchscreen.touchStart(mg.x + mg.width * 0.8, mg.y + mg.height / 2);
  for (let i = 1; i <= 8; i++) { await page.touchscreen.touchMove(mg.x + mg.width * (0.8 - 0.07 * i), mg.y + mg.height / 2); await sleep(25); }
  await page.touchscreen.touchEnd(); await sleep(500);
  out.m6c_modal_swipe = { before, after: await page.evaluate(() => galleryIdx), count: await page.evaluate(() => document.getElementById('modalPhotoCount').textContent), modalOpen: await page.evaluate(() => document.body.classList.contains('gp-modal-open')) };
  await page.keyboard.press('ArrowLeft'); await sleep(300);
  out.m6d_modal_keyboard = { after: await page.evaluate(() => galleryIdx) };
  await page.evaluate(() => { if (typeof closeModal === 'function') closeModal(); }); await sleep(300);
  // Botón "Ver detalle" de otra tarjeta abre la ficha
  const vd = await page.evaluate(() => { const c = [...document.querySelectorAll('.v-card')].find(x => x.id !== 'qaCard'); c.id = 'qaCardVD'; const b = [...c.querySelectorAll('.v-card-body button')].find(b => /detalle|cotizar/i.test(b.textContent)); b.id = 'qaVD'; return b.textContent.trim(); });
  await page.evaluate(() => document.getElementById('qaCardVD').scrollIntoView({ block: 'center', behavior: 'instant' })); await sleep(350);
  const vb = await (await page.$('#qaVD')).boundingBox();
  await page.touchscreen.tap(vb.x + vb.width / 2, vb.y + vb.height / 2); await sleep(700);
  out.m6e_ver_detalle = { label: vd, modalOpen: await page.evaluate(() => document.body.classList.contains('gp-modal-open')), form: await page.evaluate(() => !!document.getElementById('plasiForm')) };
  await page.evaluate(() => { if (typeof closeModal === 'function') closeModal(); }); await sleep(300);

  // Scroll vertical iniciando sobre otra tarjeta: la página baja, la foto no cambia
  const sel2 = await page.evaluate(() => { const c = [...document.querySelectorAll('.v-card')].find(x => x.id !== 'qaCard' && +x.querySelector('.v-card-gal').dataset.n > 1); c.id = 'qaCard2'; return '#qaCard2'; });
  const b2 = await locate(sel2);
  const py = await page.evaluate(() => window.scrollY);
  await page.touchscreen.touchStart(b2.x + b2.width / 2, b2.y + b2.height * 0.7);
  for (let i = 1; i <= 12; i++) { await page.touchscreen.touchMove(b2.x + b2.width / 2, b2.y + b2.height * 0.7 - 25 * i); await sleep(30); }
  await page.touchscreen.touchEnd(); await sleep(600);
  out.m7_vertical_scroll = Object.assign(await state(sel2), { pageScrolled: await page.evaluate(py => window.scrollY > py + 40, py) });

  // ── ESCRITORIO ──
  await page.emulate({ viewport: { width: 1280, height: 900, deviceScaleFactor: 1, isMobile: false, hasTouch: false }, userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36' });
  await page.goto(URL, { waitUntil: 'networkidle2', timeout: 60000 });
  await page.waitForSelector('.v-card .v-card-gal', { timeout: 20000 }); await sleep(500);
  const dsel = await page.evaluate(() => { const c = [...document.querySelectorAll('.v-card')].find(x => +x.querySelector('.v-card-gal').dataset.n > 1); c.id = 'qaCardD'; return '#qaCardD'; });
  out.d1_before_hover = await state(dsel);
  const db = await locate(dsel);
  await page.mouse.move(db.x + db.width / 2, db.y + db.height / 2); await sleep(500);
  out.d2_hover = await state(dsel);
  try { await (await page.$(dsel + ' .v-card-img-wrap')).screenshot({ path: 'card_desktop_hover.png' }); } catch (e) { console.error('shot hover', e.message.slice(0, 100)); }
  const dn = await (await page.$(dsel + ' .v-card-nav.next')).boundingBox();
  await page.mouse.click(dn.x + dn.width / 2, dn.y + dn.height / 2); await sleep(900);
  await page.mouse.click(dn.x + dn.width / 2, dn.y + dn.height / 2); await sleep(900);
  out.d3_after_2_clicks = await state(dsel);
  try { await (await page.$(dsel + ' .v-card-img-wrap')).screenshot({ path: 'card_desktop_3.png' }); } catch (e) { console.error('shot d3', e.message.slice(0, 100)); }
  await page.mouse.move(5, 5); await sleep(500);
  out.d4_mouse_out = { navNext: (await state(dsel)).navNext };

  await page.evaluate(sel => document.querySelector(sel + ' .v-card-nav.prev').focus(), dsel);
  await page.keyboard.press('Enter'); await sleep(700);
  out.d5_keyboard_arrow = await state(dsel);
  await page.evaluate(sel => document.querySelector(sel).focus(), dsel);
  await page.keyboard.press('Enter'); await sleep(600);
  out.d6_keyboard_card = await page.evaluate(() => ({ modalOpen: document.body.classList.contains('gp-modal-open'), galleryIdx: typeof galleryIdx !== 'undefined' ? galleryIdx : null, modalCount: document.getElementById('modalPhotoCount').textContent }));

  out.errors = errors;
  fs.writeFileSync('result.json', JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out));
  await browser.close();
})().catch(e => { console.error('FALLO', e); process.exit(1); });
