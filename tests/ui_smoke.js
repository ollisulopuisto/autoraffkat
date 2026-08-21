'use strict';

/* Käyttöliittymän savutesti.
 *
 * `node --check` tarkistaa vain syntaksin, eikä siis huomaa määrittelemätöntä
 * muuttujaa. Sellainen pääsi kerran läpi: renderAudio viittasi poistettuun
 * `busy`-muuttujaan, jolloin koko piirto keskeytyi ja "Lue uudestaan" jäi
 * ikuisesti kehräämään.
 *
 * Tämä lataa i18n.js:n ja app.js:n valeselaimeen ja ajaa jokaisen
 * piirtofunktion oikealla palvelimen tuottamalla tilarakenteella. Mikä tahansa
 * ajonaikainen virhe kaataa testin.
 *
 * Käyttö: node ui_smoke.js <static-hakemisto> <state.json> <latest.json>
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const [staticDir, statePath, latestPath] = process.argv.slice(2);

/* Kaikki luodut elementit talteen, jotta niiden käsittelijät voidaan
   laukaista. Renderöinti yksin kattaa vain puolet koodista: klikkaukset ja
   kentät ovat se toinen puoli, ja juuri sieltä löytyy viittaus muuttujaan
   jota ei ole. */
const created = [];

function makeElement(tag) {
  const el = {
    _handlers: {},
    tagName: (tag || 'div').toUpperCase(),
    children: [],
    dataset: {},
    /* style on tavallinen olio, mutta app.js asettaa CSS-muuttujia:
       ilman setPropertya piirto kaatuisi tähän. */
    style: { setProperty(name, value) { this[name] = value; },
             removeProperty(name) { delete this[name]; },
             getPropertyValue(name) { return this[name] || ''; } },
    attributes: {},
    _text: '',
    innerHTML: '',
    get textContent() { return this._text; },
    set textContent(value) { this._text = String(value); this.children = []; },
    classList: {
      _set: new Set(),
      add(...n) { n.forEach((x) => this._set.add(x)); },
      remove(...n) { n.forEach((x) => this._set.delete(x)); },
      toggle(n, on) { if (on === undefined ? !this._set.has(n) : on) this._set.add(n); else this._set.delete(n); },
      contains(n) { return this._set.has(n); },
    },
    append(...nodes) { nodes.forEach((n) => this.children.push(n)); },
    appendChild(node) { this.children.push(node); return node; },
    remove() {},
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    addEventListener(type, fn) {
      (this._handlers[type] = this._handlers[type] || []).push(fn);
    },
    removeEventListener() {},
    querySelectorAll() { return []; },
    querySelector() { return null; },
    getContext() {
      return new Proxy({}, { get: () => () => {}, set: () => true });
    },
    focus() {},
    /* Sijainti ja koko: piuhat mitataan merkkien reunoista, joten ilman
       tätä drawCables palaisi heti eikä testaisi mitään. Jokainen elementti
       saa oman y:n, jotta kaari lasketaan oikeasti. */
    getBoundingClientRect() {
      const y = (this._index || 0) * 40;
      return { left: 40, top: y, right: 140, bottom: y + 20,
               width: 100, height: 20, x: 40, y };
    },
    // Canvas-mitat, jotta drawBar laskee jotain järkevää.
    clientWidth: 1200,
    width: 1200,
    height: 120,
  };
  el.classList._set = new Set();
  el._index = created.length;
  created.push(el);
  return el;
}

/* Laukaisee kaikki tallennetut käsittelijät. Poikkeukset kerätään, koska yksi
   rikkinäinen käsittelijä ei saa estää muiden testaamista. */
let fired = 0;

function fireAll(report) {
  const snapshot = created.slice();
  for (const el of snapshot) {
    for (const [type, handlers] of Object.entries(el._handlers)) {
      for (const fn of handlers) {
        fired += 1;
        try {
          fn.call(el, { target: el, preventDefault() {}, metaKey: false,
                        ctrlKey: false, key: 'a' });
        } catch (err) {
          report(`${el.tagName}.${type}`, err);
        }
      }
    }
  }
  /* Sukupolvi kerrallaan: osa käsittelijöistä piirtää koko raitalistan
     uudestaan, jolloin syntyy uusi joukko elementtejä. Jos irronneiden
     rivien käsittelijät laukaistaan vielä seuraavallakin kierroksella, määrä
     kasvaa kierros kierrokselta eksponentiaalisesti eikä kerro mitään uutta:
     seuraava kierros piirtää saman käyttöliittymän joka tapauksessa. */
  created.length = 0;
}

const registry = new Map();
const document = {
  createElement: (tag) => makeElement(tag),
  createTextNode: (text) => ({ nodeType: 3, textContent: String(text) }),
  getElementById(id) {
    if (!registry.has(id)) registry.set(id, makeElement('div'));
    return registry.get(id);
  },
  querySelector() { return makeElement('div'); },
  querySelectorAll(selector) {
    // renderStatic kysyy [data-t]-elementit; palautetaan muutama.
    if (selector === '[data-t]') {
      return [
        Object.assign(makeElement('h2'), { dataset: { t: 'app.tracks' } }),
        Object.assign(makeElement('h3'), { dataset: { t: 'app.audio' } }),
      ];
    }
    return [];
  },
  addEventListener() {},
};

const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
const latest = JSON.parse(fs.readFileSync(latestPath, 'utf8'));

/* Reititetty fetch: app.js kutsuu boot():ia latautuessaan, joten tyhjä
   vastaus kaataisi piirron ennen kuin testi ehtii tehdä mitään. Samalla myös
   boot() tulee ajetuksi oikeasti. */
const routes = {
  '/api/state': () => state,
  '/api/settings': () => latest,
  '/api/plugins': () => ({ plugins: [{ name: 'Example', path: '/x/Example.vst3' }] }),
  '/api/defaults': () => ({ globals: state.globals, audio: state.audio }),
  '/api/language': () => ({ language: 'fi', languages: ['fi', 'en'] }),
  '/api/export': () => ({ ok: true, path: '/x/out.fcpxml', cuts: 3, warnings: [] }),
  '/api/mix': () => ({ ok: true, running: true }),
  '/api/open': () => state,
  '/api/reload': () => state,
};

const context = {
  document,
  window: { addEventListener() {}, devicePixelRatio: 2 },
  getComputedStyle: () => ({ getPropertyValue: () => '#123456' }),
  fetch: async (url) => {
    const key = Object.keys(routes).find((r) => String(url).startsWith(r));
    if (!key) throw new Error(`savutesti: tuntematon reitti ${url}`);
    return { ok: true, json: async () => routes[key](), text: async () => '' };
  },
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  AbortController: function () { this.abort = () => {}; this.signal = null; },
  console,
  JSON,
  Math,
  Object,
  Number,
  String,
  Array,
  Boolean,
  Date,
  encodeURIComponent,
};
context.globalThis = context;
vm.createContext(context);

for (const name of ['i18n.js', 'app.js']) {
  vm.runInContext(fs.readFileSync(path.join(staticDir, name), 'utf8'), context,
                  { filename: name });
}

/* Kattavuusmittari: jokainen ylätason funktio kääritään laskuriin. Näin
   testi kertoo suoraan mitä se EI aja — uusi funktio jota kukaan ei kutsu on
   juuri se paikka johon seuraava ajonaikainen virhe piiloutuu. */
const calls = new Map();
const NEVER_CALLED_OK = new Set([
  'boot',        // ajetaan app.js:n latauksessa, ennen käärimistä
  'setLang',     // kutsutaan suoraan testistä ennen käärimistä
  'T',           // sama
]);

for (const name of Object.keys(context)) {
  const value = context[name];
  if (typeof value !== 'function' || !/^[a-z]/.test(name)) continue;
  if (['fetch', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval',
       'getComputedStyle', 'encodeURIComponent'].includes(name)) continue;
  calls.set(name, 0);
  context[name] = function wrapped(...args) {
    calls.set(name, calls.get(name) + 1);
    return value.apply(this, args);
  };
}

let failures = 0;
function run(label, fn) {
  try {
    fn();
  } catch (err) {
    failures += 1;
    console.error(`  ${label}: ${err.name}: ${err.message}`);
  }
}

/* Molemmat kielet ja molemmat tilat: käsittely päällä ja pois. */
for (const lang of ['fi', 'en']) {
  for (const audioOn of [false, true]) {
    /* app.js:n `let state` on leksikaalinen sidos eikä globaalin objektin
       ominaisuus, joten sille on sijoitettava skriptin sisältä. */
    const fresh = JSON.parse(JSON.stringify(state));
    fresh.audio.enabled = audioOn;
    fresh.audio.duck = audioOn;
    fresh.audio.declick = audioOn;
    fresh.audio.room_track = audioOn && fresh.tracks.length ? fresh.tracks[0].key : '';
    context.__state = fresh;
    context.__latest = JSON.parse(JSON.stringify(latest));
    vm.runInContext('state = __state; latest = __latest;', context);
    context.setLang(lang);

    const tag = `${lang}/${audioOn ? 'audio' : 'plain'}`;
    run(`${tag} renderStatic`, () => context.renderStatic());
    run(`${tag} renderLanguage`, () => context.renderLanguage());
    run(`${tag} renderHeader`, () => context.renderHeader());
    run(`${tag} renderTracks`, () => context.renderTracks());
    run(`${tag} renderGlobals`, () => context.renderGlobals());
    run(`${tag} renderAudio`, () => context.renderAudio());
    run(`${tag} renderLegend`, () => context.renderLegend());
    run(`${tag} renderCuts`, () => context.renderCuts());
    run(`${tag} renderRuler`, () => context.renderRuler());
    run(`${tag} drawBar`, () => context.drawBar());
    run(`${tag} payload`, () => {
      const body = context.payload();
      if (!body.tracks || !body.globals || !body.audio) {
        throw new Error('payload puuttuu kenttiä');
      }
    });
    /* Käsittelijät: klikkaukset, valinnat ja kenttien muutokset. Nämä ovat
       koodia jota pelkkä piirto ei aja lainkaan. */
    run(`${tag} käsittelijät`, () => {
      let first = null;
      fireAll((where, err) => { if (!first) first = new Error(`${where}: ${err.message}`); });
      if (first) throw first;
    });

    /* Käsittelyn ollessa kesken piirto menee eri haaraan. */
    fresh.mix = Object.assign({}, fresh.mix, {
      progress: { done: 1, total: 4, current: 'mic.wav', eta: 120, running: true },
    });
    vm.runInContext('state = __state;', context);
    run(`${tag} renderAudio (kesken)`, () => context.renderAudio());
  }
}

/* Virhehaarat: puuttuva tiedosto, verhokäyrävirhe, ongelmalista ja tyhjä
   tulos. Nämä piirtyvät eri koodipolkua kuin onnistunut tila. */
{
  const broken = JSON.parse(JSON.stringify(state));
  broken.tracks.forEach((t) => {
    t.missing = true;
    t.envelope_error = 'purku epäonnistui';
    t.parts = (t.parts || []).map((p) => Object.assign({}, p, { missing: true }));
  });
  broken.mix = Object.assign({}, broken.mix, {
    errors: ['jokin meni pieleen'], ready: 0, room: 0, gains: {},
  });
  broken.inherited_from = '/x/edellinen.autoraffkat.json';
  context.__state = broken;
  context.__latest = { ok: false, problems: ['puuttuu jotain'], preview: null };
  vm.runInContext('state = __state; latest = __latest;', context);
  run('virhetila renderTracks', () => context.renderTracks());
  run('virhetila renderAudio', () => context.renderAudio());
  run('virhetila renderHeader', () => context.renderHeader());
  run('virhetila renderCuts', () => context.renderCuts());
  run('virhetila renderLegend', () => context.renderLegend());
  run('virhetila drawBar', () => context.drawBar());
  run('virhetila käsittelijät', () => {
    let first = null;
    fireAll((where, err) => { if (!first) first = new Error(`${where}: ${err.message}`); });
    if (first) throw first;
  });
}

/* Asynkroniset polut: pyyntökierros, vienti ja edistymisen seuranta. Näitä
   piirto ei aja lainkaan, ja juuri ne koskevat palvelinta. */
async function asyncPaths() {
  await step('send', () => context.send());
  await step('exportXml', () => context.exportXml());
  await step('openXml', () => context.openXml('/x/test.fcpxml'));
  await step('runMix', () => context.runMix());
  await step('resetSection(globals)', () => context.resetSection('globals'));
  await step('resetSection(audio)', () => context.resetSection('audio'));
  step('banner', () => { context.banner('viesti'); context.banner('virhe', true);
                         context.banner(''); });
  step('redrawAll', () => context.redrawAll());
  step('watchMix', () => context.watchMix());
  /* watchProgress haarautuu sen mukaan onko laskenta valmis. */
  for (const ready of [true, false]) {
    context.__state.progress = { done: 1, total: 4, current: 'x', ready };
    vm.runInContext('state = __state;', context);
    step(`watchProgress(ready=${ready})`, () => context.watchProgress());
  }
}

async function step(label, fn) {
  try {
    await fn();
  } catch (err) {
    failures += 1;
    console.error(`  ${label}: ${err.name}: ${err.message}`);
  }
}

(async () => {
await asyncPaths();

const never = [...calls.entries()].filter(([n, c]) => c === 0 && !NEVER_CALLED_OK.has(n))
  .map(([n]) => n);
if (never.length) {
  console.error('savutesti ei aja näitä funktioita: ' + never.join(', '));
  console.error('lisää ne testiin tai NEVER_CALLED_OK-listaan perusteluineen');
  process.exit(1);
}

/* Vartio itse vartijalle: jos käsittelijöitä ei laukea, testi ei testaa
   niistä mitään ja menisi läpi tyhjänä. */
const MIN_HANDLERS = 200;
if (fired < MIN_HANDLERS) {
  console.error(`vain ${fired} käsittelijää laukesi, odotettiin ${MIN_HANDLERS}+`);
  process.exit(1);
}

if (failures) {
  console.error(`${failures} virhettä`);
  process.exit(1);
}
console.log(`ui_smoke: ok (${fired} käsittelijää, `
            + `${calls.size} funktiota katettu)`);
})();
