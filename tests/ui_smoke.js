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

/* Tunnukselliset elementit talteen, jotta $() löytää juuri sen elementin
   jonka koodi loi. Ilman tätä getElementById palautti aina uuden tyhjän
   divin, ja paikallaan vaihtaminen näytti onnistuvan tekemättä mitään. */
const registry = new Map();

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
    _id: '',
    innerHTML: '',
    get id() { return this._id; },
    set id(value) { this._id = String(value); registry.set(this._id, this); },
    get textContent() { return this._text; },
    set textContent(value) { this._text = String(value); this.children = []; },
    classList: {
      _set: new Set(),
      add(...n) { n.forEach((x) => this._set.add(x)); },
      remove(...n) { n.forEach((x) => this._set.delete(x)); },
      toggle(n, on) { if (on === undefined ? !this._set.has(n) : on) this._set.add(n); else this._set.delete(n); },
      contains(n) { return this._set.has(n); },
    },
    append(...nodes) {
      nodes.forEach((n) => { if (n && typeof n === 'object') n._parent = this;
                             this.children.push(n); });
    },
    appendChild(node) {
      if (node && typeof node === 'object') node._parent = this;
      this.children.push(node);
      return node;
    },
    /* Paikallaan vaihtaminen on oikea DOM-toiminto ja app.js käyttää sitä,
       jotta säätimiä ei piirretä uudestaan kesken raahauksen. */
    replaceWith(node) {
      const parent = this._parent;
      if (!parent) return;
      const at = parent.children.indexOf(this);
      if (at >= 0) parent.children[at] = node;
      node._parent = parent;
      if (this._id) registry.set(this._id, node);
    },
    remove() {},
    /* Osoittimen kaappaus on oikea DOM-toiminto, ja zoomaus käyttää sitä.
       Ilman näitä vetokäsittelijä kaatuu vain testissä. */
    setPointerCapture() {},
    releasePointerCapture() {},
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
    /* Oikeassa <input>:issä on select(); ilman tätä kentän
       kohdistuskäsittelijä kaatuisi tyngässä eikä oikeassa selaimessa. */
    select() {},
    /* Sijainti ja koko: esikatselupalkki ja viivain laskevat leveydestä,
       joten ilman tätä ne palaisivat heti eivätkä testaisi mitään. */
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

/* Raahauksen dataTransfer. Kytkentätaulu kirjoittaa siihen raidan avaimen ja
   lukee sen pudotuksessa; ilman tätä koko raahauspolku jäisi ajamatta. */
function transfer() {
  const data = {};
  return {
    effectAllowed: '', dropEffect: '',
    setData(type, value) { data[type] = String(value); },
    getData(type) { return data[type] || ''; },
  };
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
          /* Tapahtuma on se mitä selain antaa, ei vähempää: puuttuva
             `stopPropagation` näkyisi tuotannossa vasta klikkauksena joka
             tekee kaksi asiaa yhtä aikaa, eikä testi kertoisi siitä. */
          /* Osoitin- ja rullatapahtumien kentät mukaan: zoomaus lukee
             `clientX`:n ja `deltaY`:n, ja ilman niitä laskenta tuottaa
             NaN:ia jota mikään ei huomaisi. */
          fn.call(el, { target: el, preventDefault() {}, stopPropagation() {},
                        metaKey: false, ctrlKey: false, key: 'a',
                        clientX: 120, clientY: 40, deltaY: -100, pointerId: 1,
                        dataTransfer: transfer() });
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
  '/api/plugin-params': () => ({
    total: 9,
    params: [
      { name: 'mix', label: 'Mix', type: 'float', min: 0, max: 100, step: 0.1,
        value: 50, units: '%' },
      { name: 'bypass', label: 'Bypass', type: 'bool', value: false },
      { name: 'mode', label: 'Mode', type: 'choice', value: 'Voice',
        choices: ['Voice', 'Music'] },
    ],
  }),
  '/api/defaults': () => ({ globals: state.globals, audio: state.audio }),
  '/api/language': () => ({ language: 'fi', languages: ['fi', 'en'] }),
  '/api/export': () => ({ ok: true, path: '/x/out.fcpxml', cuts: 3, warnings: [] }),
  '/api/mix': () => ({ ok: true, running: true }),
  /* Mittauksen käynnistys palauttaa koko tilan, kuten palvelin. `running`
     on false, koska true panisi `watchVideo`n kyselemään uudestaan
     loputtomiin — testi ajaa sen kerran ja se riittää. */
  '/api/video': () => state,
  '/api/pick': () => ({ path: '/x/valittu.fcpxml' }),
  '/api/open': () => state,
  '/api/pan-auto': () => state,
  '/api/final-cut': () => ({ ok: true }),
  '/api/reveal': () => ({ ok: true }),
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

    /* Kytkentätaulu: kortin siirto paikasta toiseen. Piirto yksin ei aja
       sijoitusta lainkaan, ja juuri sijoitus on se joka kirjoittaa raidan
       roolin ja puhujan — eli koko taulun tarkoitus. */
    run(`${tag} kytkentätaulu`, () => {
      const video = fresh.tracks.find((t) => t.kind === 'video');
      const audio = fresh.tracks.find((t) => t.kind === 'audio');
      if (!video || !audio) return;
      context.assign(video, { kind: 'shared', side: 'video', name: '' });
      context.assign(audio, { kind: 'shared', side: 'audio', name: '' });
      if (fresh.audio.room_track !== audio.key) throw new Error('tilaääni ei asettunut');
      context.assign(audio, { kind: 'tray', side: 'any', name: '' });
      if (audio.config.role !== 'unused') throw new Error('varastoon jäi rooli');
      const name = context.newSpeakerName();
      context.assign(video, { kind: 'speaker', side: 'video', name });
      context.assign(audio, { kind: 'speaker', side: 'audio', name });
      const { slots } = context.buildSlots();
      if (!slots.some((sl) => sl.video.length && sl.audio.length)) {
        throw new Error('pari ei päätynyt samaan paikkaan');
      }
      context.pickUp(video);
      context.renderTracks();
      context.pickUp(video);
    });

    /* Käsittelyn ollessa kesken piirto menee eri haaraan. */
    fresh.mix = Object.assign({}, fresh.mix, {
      progress: {
        done: 1, total: 4, current: 'mic.wav', stage: 'plugin',
        fraction: 0.42, eta: 120, running: true,
      },
    });
    vm.runInContext('state = __state;', context);
    run(`${tag} renderAudio (kesken)`, () => context.renderAudio());

    /* Ilman osuutta palkki on määrittelemättömässä tilassa: eri haara, ja
       juuri se johon vanha palvelin tai kesken jäänyt kierros osuu. */
    fresh.mix = Object.assign({}, fresh.mix, {
      progress: { done: 0, total: 4, current: 'mic.wav', running: true },
    });
    vm.runInContext('state = __state;', context);
    run(`${tag} renderAudio (osuus tuntematon)`, () => context.renderAudio());
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
  /* Ilman polkua: selaimen haara, jossa valitsin on palvelimella. */
  await step('openXml (valitsin)', () => context.openXml());
  await step('runMix', () => context.runMix());
  await step('resetSection(globals)', () => context.resetSection('globals'));
  await step('resetSection(audio)', () => context.resetSection('audio'));
  /* Liitännäisen säätimet piirtyvät vasta kun palvelin on kertonut mitä
     liitännäisessä on, eli asynkronisen kierroksen jälkeen. Ilman tätä
     renderPluginParams ei ajaisi kuin lataushaaransa. */
  const withPlugin = JSON.parse(JSON.stringify(context.__state));
  withPlugin.audio.enabled = true;
  withPlugin.audio.plugin_path = '/x/Example.vst3';
  withPlugin.audio.plugin_params = { mix: 25 };
  withPlugin.mix = { progress: {} };
  context.__state = withPlugin;
  vm.runInContext('state = __state;', context);
  await step('loadPluginParams', () => context.loadPluginParams('/x/Example.vst3'));
  step('renderAudio (liitännäisen säätimet)', () => context.renderAudio());
  step('liitännäisen säätimien käsittelijät', () => {
    let first = null;
    fireAll((where, err) => { if (!first) first = new Error(`${where}: ${err.message}`); });
    if (first) throw first;
  });
  /* Valmis käsittely on oma tilansa, ja sen takana on vahvistus. Ilman tätä
     haaraa painikkeen kolmesta tilasta ajettaisiin vain yksi — ja juuri se
     jota käyttäjä ei näe työn jälkeen. */
  const processed = JSON.parse(JSON.stringify(context.__state));
  processed.audio.enabled = true;
  processed.mix = { progress: {}, ready: 4, fresh: 4, expected: 4, gains: {},
                    errors: [], program_trim: -1.8 };
  context.__state = processed;
  vm.runInContext('state = __state;', context);
  step('renderAudio (kaikki ajan tasalla)', () => context.renderAudio());
  step('valmis-painikkeen käsittelijät', () => {
    let first = null;
    fireAll((where, err) => { if (!first) first = new Error(`${where}: ${err.message}`); });
    if (first) throw first;
  });
  step('vahvistus ja peruutus', () => {
    context.mixConfirm = true;
    context.swapMixButton();
    let first = null;
    fireAll((where, err) => { if (!first) first = new Error(`${where}: ${err.message}`); });
    if (first) throw first;
  });
  /* Osa vanhentunut: painike kutsuu taas painamaan ja teksti kertoo miksi. */
  context.__state.mix.fresh = 2;
  vm.runInContext('state = __state;', context);
  step('renderAudio (osa vanhentunut)', () => context.renderAudio());
  step('runMix(force)', () => context.runMix(true));
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

/* Esiasetuksen säätimet näkyvät vasta mukautetussa.

   Esiasetus on valinta, ja sen alla olevat neljä lukua ovat sen määritelmä.
   Jos ne palaavat näkyviin muissa esiasetuksissa, valinta muuttuu taas
   sivutuotteena — mikä on juuri se ero jonka tämä rivitys poisti. */
/* Panoroinnin rivi piirtyy molemmissa tiloissa, eikä siinä ole säätimiä.

   Kytkin on päällä tai pois: määrä on mittauksesta johdettu vakio. Jos
   riville ilmestyy liukusäädin, se on vastuun siirtoa käyttäjälle. */
run('panorointi ilman säätimiä', () => {
  vm.runInContext('state.globals.panning = true; renderTracks(); renderGlobals();',
                  context);
  const knobs = vm.runInContext(
    "TRACK_KNOBS().map((k) => k.key).join(',')", context);
  if (knobs.includes('pan')) {
    throw new Error('panorointi ilmestyi kortin säätimeksi');
  }
  vm.runInContext('state.globals.panning = false; renderTracks(); renderGlobals();',
                  context);
});

run('esiasetuksen säätimet', () => {
  const count = (rhythm) => {
    let n = 0;
    vm.runInContext(`state.globals.rhythm = '${rhythm}'; renderGlobals();`, context);
    const host = vm.runInContext("document.getElementById('global-list')", context);
    for (const child of host.children || []) {
      if (child.className === 'knob') n += 1;
    }
    return n;
  };
  const preset = count('broadcast');
  const custom = count('custom');
  if (preset !== 0) {
    throw new Error(`esiasetuksessa näkyi ${preset} säädintä, pitäisi olla 0`);
  }
  if (custom < 3) {
    throw new Error(`mukautetussa näkyi ${custom} säädintä, odotettiin 3+`);
  }
});

/* Siirretään yksi säädin pois mitatusta oletuksesta, jotta merkin voi
   todeta. Ilman tätä fixture on identtinen oletusten kanssa eikä testi
   kertoisi merkistä mitään suuntaan tai toiseen. */
run('poikkeamamerkki', () => {
  vm.runInContext(`
    state.audio.enabled = true;
    state.audio.duck = true;
    state.audio.duck_hold = (state.audio_defaults.duck_hold || 0) + 0.5;
    OPEN_ROWS.clear();
    renderAudio();
  `, context);
});

/* Kytkin ei saa olla avauspainikkeen sisällä.

   Sisällä se tarkoittaisi kahta asiaa kerralla: klikkaus kytkimeen avaisi
   myös rivin, ja korostus lupaisi taas yhtä kohdetta kahden sijaan. Se on
   myös kelvotonta HTML:ää — painikkeen sisällä ei ole säätimiä. */
{
  const openers = created.filter((el) => el.className === 'arow-open');
  if (!openers.length) {
    console.error('avauspainikkeita ei löytynyt');
    process.exit(1);
  }
  const nested = openers.filter((el) => (el.children || [])
    .some((c) => c.type === 'checkbox'));
  if (nested.length) {
    console.error(`${nested.length} avauspainiketta sisältää kytkimen`);
    process.exit(1);
  }
}

/* Ääni-paneelin rivit rakenteena, ei vain «ei kaatunut».

   Poikkeamamerkki on koko avautuvan rivin ehto: suljettu rivi ei saa
   piilottaa sitä, että joku on jo siirtänyt sen sisällä olevaa säädintä.
   Se on juuri sellainen ominaisuus joka rapistuu huomaamatta — rivi
   piirtyy, mitään ei kaadu, ja merkki on vain poissa. */
{
  const rows = created.filter((el) => el.className === 'arow'
    || el.classList._set.has('arow'));
  if (rows.length < 4) {
    console.error(`ääni-paneelissa ${rows.length} avautuvaa riviä, odotettiin 4+`);
    process.exit(1);
  }
  const marked = rows.filter((el) => el.classList._set.has('deviates'));
  if (!marked.length) {
    console.error('yksikään rivi ei merkinnyt poikkeamaa, vaikka tila '
                  + 'poikkeaa oletuksista — merkki on rivin koko idea');
    process.exit(1);
  }
  const named = created.filter((el) => el.className === 'arow-dev'
    && String(el.textContent || '').trim().length);
  if (!named.length) {
    console.error('poikkeamamerkki ei nimennyt mitään säädintä');
    process.exit(1);
  }
}

if (failures) {
  console.error(`${failures} virhettä`);
  process.exit(1);
}
console.log(`ui_smoke: ok (${fired} käsittelijää, `
            + `${calls.size} funktiota katettu)`);
})();
