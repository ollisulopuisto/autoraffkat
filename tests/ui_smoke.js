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

function makeElement(tag) {
  const el = {
    tagName: (tag || 'div').toUpperCase(),
    children: [],
    dataset: {},
    style: {},
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
    addEventListener() {},
    removeEventListener() {},
    querySelectorAll() { return []; },
    querySelector() { return null; },
    getContext() {
      return new Proxy({}, { get: () => () => {}, set: () => true });
    },
    focus() {},
    // Canvas-mitat, jotta drawBar laskee jotain järkevää.
    clientWidth: 1200,
    width: 1200,
    height: 120,
  };
  el.classList._set = new Set();
  return el;
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
    /* Käsittelyn ollessa kesken piirto menee eri haaraan. */
    fresh.mix = Object.assign({}, fresh.mix, {
      progress: { done: 1, total: 4, current: 'mic.wav', eta: 120, running: true },
    });
    vm.runInContext('state = __state;', context);
    run(`${tag} renderAudio (kesken)`, () => context.renderAudio());
  }
}

if (failures) {
  console.error(`${failures} virhettä`);
  process.exit(1);
}
console.log('ui_smoke: ok');
