'use strict';

/* Käyttöliittymän koko logiikka. Säätimen liike lähettää asetukset
   palvelimelle, joka ajaa vain päätöskerroksen ja palauttaa leikkauslistan ja
   esikatselun. Pyynnöt niputetaan ja edellinen keskeytetään, jotta nopea
   raahaus ei kasaa jonoa. */

const SPEAKER_COLORS = ['--sp0', '--sp1', '--sp2', '--sp3', '--sp4'];
const WIDE_COLOR = '--wide';
const DEBOUNCE_MS = 45;

/* Mikkimerkki peilikuvan päälle. Merkkijonona, koska SVG-solmu vaatisi
   createElementNS:n eikä toisi tähän mitään. */
const MIC_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
  + ' stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
  + '<rect x="9" y="2" width="6" height="11" rx="3"/>'
  + '<path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/></svg>';

const TRACK_KNOBS = () => [
  { key: 'sensitivity_db', label: T('knob.sensitivity'), min: 0, max: 40, step: 0.5,
    unit: T('knob.sensitivityUnit') },
  { key: 'gain_db', label: T('knob.gain'), min: -24, max: 24, step: 0.5,
    unit: T('unit.db') },
];

const RHYTHM_PRESETS = () => [
  ['broadcast', T('rhythm.broadcast'), T('rhythm.broadcastHint')],
  ['mellow', T('rhythm.mellow'), T('rhythm.mellowHint')],
  ['hectic', T('rhythm.hectic'), T('rhythm.hecticHint')],
  ['custom', T('rhythm.custom'), T('rhythm.customHint')],
];

const PRESET_VALUES = {
  broadcast: { min_shot: 2.5, lead: 0.30, hang: 0.60, wide_every: 14.0, wide_hold: 3.5 },
  mellow: { min_shot: 4.5, lead: 0.15, hang: 1.00, wide_every: 22.0, wide_hold: 4.5 },
  hectic: { min_shot: 1.4, lead: 0.40, hang: 0.25, wide_every: 8.0, wide_hold: 2.0 },
};

const GLOBAL_KNOBS = () => [
  { key: 'min_shot', label: T('knob.minShot'), min: 0.3, max: 12, step: 0.1,
    unit: T('unit.seconds') },
  { key: 'lead', label: T('knob.lead'), min: 0, max: 1.5, step: 0.01,
    unit: T('unit.seconds') },
  { key: 'hang', label: T('knob.hang'), min: 0, max: 2.0, step: 0.02,
    unit: T('unit.seconds') },
  { key: 'confirm', label: T('knob.confirm'), min: 0, max: 2, step: 0.02,
    unit: T('unit.seconds') },
];

const LONGTAKE_KNOBS = () => [
  { key: 'wide_every', label: T('knob.wideEvery'), min: 0, max: 90, step: 1,
    unit: T('unit.seconds'), zero: T('knob.never') },
  { key: 'wide_hold', label: T('knob.wideHold'), min: 0.5, max: 30, step: 0.5,
    unit: T('unit.seconds') },
];

const LONGTAKE_RULES = () => [
  ['return', T('longtake.return'), T('longtake.returnHint')],
  ['stay', T('longtake.stay'), T('longtake.stayHint')],
  ['reaction', T('longtake.reaction'), T('longtake.reactionHint')],
];

const OVERLAP_KNOBS = () => [
  { key: 'min_overlap', label: T('knob.minOverlap'), min: 0, max: 3, step: 0.05,
    unit: T('unit.seconds') },
  { key: 'dominance_db', label: T('knob.dominance'), min: 0, max: 24, step: 0.5,
    unit: T('unit.db') },
];

const AUDIO_KNOBS = () => [
  { key: 'target_lufs', label: T('audio.targetLufs'), min: -32, max: -10, step: 0.5,
    unit: T('unit.lufs') },
  { key: 'high_pass_hz', label: T('audio.highpass'), min: 0, max: 200, step: 5,
    unit: T('unit.hz'), zero: T('audio.highpassOff') },
  { key: 'peak_threshold_db', label: T('audio.peak'), min: -30, max: 0, step: 0.5,
    unit: T('unit.db') },
  { key: 'leveler_threshold_db', label: T('audio.leveler'), min: -36, max: 0, step: 0.5,
    unit: T('unit.db') },
  { key: 'gain_db', label: T('audio.trim'), min: -12, max: 12, step: 0.5,
    unit: T('unit.db') },
];

const DECLICK_KNOBS = () => [
  { key: 'declick_sensitivity', label: T('audio.declickSensitivity'), min: 0, max: 1,
    step: 0.05 },
];

const DUCK_KNOBS = () => [
  { key: 'duck_db', label: T('audio.duckDb'), min: -60, max: 0, step: 1,
    unit: T('unit.db'), zero: T('audio.duckNone') },
  { key: 'duck_lookahead', label: T('audio.duckLookahead'), min: 0, max: 0.5,
    step: 0.01, unit: T('unit.seconds') },
  { key: 'duck_hold', label: T('audio.duckHold'), min: 0, max: 2, step: 0.05,
    unit: T('unit.seconds') },
  { key: 'duck_min_open', label: T('audio.duckMinOpen'), min: 0, max: 1, step: 0.05,
    unit: T('unit.seconds') },
  { key: 'duck_dominance_db', label: T('audio.duckDominance'), min: 0, max: 24,
    step: 0.5, unit: T('unit.db') },
  { key: 'duck_min_closed', label: T('audio.duckMinClosed'), min: 0, max: 3, step: 0.1,
    unit: T('unit.seconds') },
  { key: 'duck_fade', label: T('audio.duckFade'), min: 0.02, max: 1, step: 0.01,
    unit: T('unit.seconds') },
  { key: 'duck_release', label: T('audio.duckRelease'), min: 0.02, max: 2, step: 0.02,
    unit: T('unit.seconds') },
];

const ROOM_KNOBS = () => [
  { key: 'room_db', label: T('audio.roomDb'), min: -40, max: 0, step: 1,
    unit: T('audio.roomDbUnit') },
];

const OVERLAP_RULES = () => [
  ['wide', T('overlap.wide'), T('overlap.wideHint')],
  ['hold', T('overlap.hold'), T('overlap.holdHint')],
  ['louder', T('overlap.louder'), T('overlap.louderHint')],
];

let state = null;               // /api/state
let latest = null;              // viimeisin laskettu tulos
let pending = null;             // ajastin
let inflight = null;            // AbortController
let progressTimer = null;
let mixTimer = null;
// Onko uudelleenkäsittely varmistettavana. Moduulitasolla, koska painike
// vaihdetaan paikallaan eikä tila saa kadota vaihdon mukana.
let mixConfirm = false;
let pluginList = null;          // haetaan kerran, samat koko koneella
let pluginParams = {};          // polku -> säätimet, tai 'loading' / 'error'

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement)
  .getPropertyValue(name).trim();

/* Aika muotoon m:ss.ss tai h:mm:ss.ss. Sekunnin sadasosat ovat mukana, koska
   leikkauskohdan tarkkuus on se mitä listasta halutaan lukea. */
function fmtTime(seconds) {
  const sign = seconds < 0 ? '-' : '';
  const s = Math.abs(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = (s % 60);
  const mm = String(m).padStart(2, '0');
  const ss = rest.toFixed(2).padStart(5, '0');
  return h ? `${sign}${h}:${mm}:${ss}` : `${sign}${m}:${ss}`;
}

/* ------------------------------------------------------------ säätimet */

/* Yksi liukusäädin: nimi, kahva ja lukuarvo. Arvo päivittyy heti ruudulle ja
   onChange niputetaan schedule():ssa, joten raahaus ei lähetä joka pikselistä. */
function knob(spec, value, onChange) {
  const wrap = document.createElement('div');
  wrap.className = 'knob';
  const label = document.createElement('label');
  label.textContent = spec.label;
  const input = document.createElement('input');
  input.type = 'range';
  input.min = spec.min; input.max = spec.max; input.step = spec.step;
  input.value = value;
  const out = document.createElement('output');
  const show = () => {
    const v = Number(input.value);
    out.textContent = (spec.zero && v === 0)
      ? spec.zero
      : `${spec.step < 1 ? v.toFixed(2).replace(/0$/, '') : v}${spec.unit || ''}`;
  };
  show();
  input.addEventListener('input', () => { show(); onChange(Number(input.value)); });
  wrap.append(label, input, out);
  return wrap;
}

/* Tiedostokoko ja kesto lyhyesti. Tarkkuus ei auta: erot ovat suuruusluokkia. */
function fmtSize(bytes) {
  if (!bytes) return '';
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${Math.round(bytes / 1e6)} MB`;
  return `${Math.round(bytes / 1e3)} kt`;
}

function fmtDuration(seconds) {
  if (!seconds) return '';
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? T('unit.hourMin', { h, m }) : T('unit.min', { n: m });
}

/* Raidan tekniset tiedot yhdeksi riviksi. Kuvalle mitat ja bitit, äänelle
   kanavat ja bittisyvyys — eli se mistä huomaa jos jokin on väärin. */
function trackFacts(media) {
  const p = media.probe || {};
  const bits = [];
  if (p.video) {
    const v = p.video;
    bits.push(`${v.width}×${v.height}`);
    if (v.fps) bits.push(`${Math.round(v.fps * 100) / 100} fps`);
    if (v.codec) bits.push(v.codec);
    if (v.bitrate) bits.push(`${Math.round(v.bitrate / 1e6)} Mb/s`);
  }
  if (p.audio) {
    const a = p.audio;
    if (!p.video) {
      if (a.codec) bits.push(a.codec);
      bits.push(T('app.channels', { n: a.channels }));
      if (a.rate) bits.push(`${Math.round(a.rate / 1000)} kHz`);
      if (a.depth) bits.push(`${Math.round(a.depth)} bit`);
      if (!a.depth && a.bitrate) bits.push(`${Math.round(a.bitrate / 1000)} kb/s`);
    } else {
      bits.push(T('app.audioOf', { codec: a.codec, n: a.channels }));
    }
  }
  const span = [fmtDuration(media.total_duration), fmtSize(media.total_size)]
    .filter(Boolean).join(' · ');
  if (span) bits.push(span);
  if (media.angle_name) bits.push(T('app.angle', { name: media.angle_name }));
  if ((media.parts || []).length > 1) bits.push(T('app.parts', { n: media.parts.length }));
  return bits.join(' · ');
}

/* ------------------------------------------------------------ raidat */

/* Puhujan indeksi väripaletissa. Tulee palvelimen esikatselusta, jotta palkki,
   selite ja leikkauslista käyttävät varmasti samaa väriä. */
function speakerIndex(name) {
  if (!name) return -1;
  if (latest && latest.preview && latest.preview.speakers) {
    const found = latest.preview.speakers.find((s) => s.name === name);
    if (found) return found.index;
  }
  if (!state || !state.tracks) return 0;
  const speakers = [...new Set(state.tracks.map((t) => t.config.speaker).filter(Boolean))];
  const idx = speakers.indexOf(name);
  return idx >= 0 ? idx : 0;
}

/* Kytkentätaulu.

   Rivi on paikka, ei raita: vasemmalla kuva, oikealla ääni ja välissä puhujan
   nimi. Pari syntyy siitä missä kortti on, joten roolia ei valita erikseen —
   kortin siirtäminen paikkaan asettaa sekä roolin että puhujan, ja nimi
   kirjoitetaan kerran paikkaan eikä kahdesti raitoihin.

   Piuha on vaakasuora viiva kahden vierekkäisen solun välissä. Sitä ei
   tarvitse mitata eikä piirtää uudestaan, koska päät ovat aina samalla
   korkeudella: siksi tässä ei enää ole piuhakerrosta eikä sijaintien
   mittausta. Ristiin menevä piuha ei ole tässä esityksessä mahdollinen.

   Ylin rivi on niille raidoille joilla ei ole puhujaa: laaja kuva ja
   tilaääni. Ne koskevat koko jaksoa samalla tavalla kuin muut rivit koskevat
   yhtä ihmistä. */

/* Nostettu kortti. Raahaus ja klikkaus kirjoittavat molemmat tähän ja pudotus
   lukee sen: sama reitti hiirellä ja näppäimistöllä, eikä pudotus jää
   dataTransferin varaan, jota selain ei anna lukea dragoverissa. */
let picked = null;

function trackByKey(key) {
  return (state.tracks || []).find((t) => t.key === key) || null;
}

/* Onko raita tilaäänenä. Tilaääni ei ole rooli vaan asetus, joten se
   kysytään erikseen. */
function isRoom(media) {
  return !!state.audio && state.audio.room_track === media.key
    && media.config.role !== 'mic';
}

/* Paikat raidoista. Nimetyt puhujat paletin järjestyksessä, jotta vasen ja
   oikea sarake ovat samassa järjestyksessä. Nimetön lähikuva tai mikki saa
   oman paikkansa: raita ei saa kadota siksi ettei sille ole vielä keksitty
   nimeä. */
function buildSlots() {
  const shared = { id: 'shared', kind: 'shared', name: '', index: -1,
                   video: [], audio: [] };
  const named = new Map();
  const loose = [];
  const tray = [];

  const slotFor = (name) => {
    if (!named.has(name)) {
      named.set(name, { id: `s:${name}`, kind: 'speaker', name,
                        index: speakerIndex(name), video: [], audio: [] });
    }
    return named.get(name);
  };

  (state.tracks || []).forEach((media) => {
    const role = media.config.role;
    const name = (media.config.speaker || '').trim();
    if (media.kind === 'video' && role === 'wide') { shared.video.push(media); return; }
    if (media.kind === 'audio' && isRoom(media)) { shared.audio.push(media); return; }
    if (role === 'close' || role === 'mic') {
      let slot;
      if (name) {
        slot = slotFor(name);
      } else {
        slot = { id: `u:${media.key}`, kind: 'speaker', name: '', index: 1e6,
                 video: [], audio: [] };
        loose.push(slot);
      }
      (media.kind === 'video' ? slot.video : slot.audio).push(media);
      return;
    }
    tray.push(media);
  });

  const speakers = [...named.values()].sort((a, b) => a.index - b.index);
  return { slots: [shared, ...speakers, ...loose], tray };
}

/* Kelpaako kortti kohteeseen. Ratkaistaan yhdessä paikassa, jotta raahaus ja
   klikkaus eivät voi olla eri mieltä. */
function accepts(dest, media) {
  if (!media) return false;
  if (dest.kind === 'tray') return true;
  return media.kind === dest.side;
}

/* Kortin siirto paikkaan. Tämä on ainoa kohta jossa rooli ja puhuja
   asetetaan: paikka kertoo molemmat. */
function assign(media, dest) {
  if (!accepts(dest, media)) return;
  const cfg = media.config;
  const audio = state.audio || {};
  if (audio.room_track === media.key) audio.room_track = '';

  if (dest.kind === 'tray') {
    cfg.role = 'unused';
    cfg.speaker = '';
  } else if (dest.kind === 'shared' && dest.side === 'video') {
    /* Laajoja on tasan yksi: edellinen putoaa käyttämättömiin. Hiljainen
       korvaaminen ei näkyisi mistään. */
    (state.tracks || []).forEach((t) => {
      if (t !== media && t.config.role === 'wide') {
        t.config.role = 'unused';
        t.config.speaker = '';
      }
    });
    cfg.role = 'wide';
    cfg.speaker = '';
  } else if (dest.kind === 'shared') {
    cfg.role = 'unused';
    cfg.speaker = '';
    audio.room_track = media.key;
  } else {
    cfg.role = media.kind === 'video' ? 'close' : 'mic';
    cfg.speaker = dest.name;
  }

  picked = null;
  renderTracks();
  renderAudio();
  renderLegend();
  schedule(0);
}

/* Uusi puhuja saa nimen heti: nimetön paikka näyttää samalta kuin rikki
   mennyt, ja nimen ehtii vaihtaa kentästä. */
function newSpeakerName() {
  const taken = new Set((state.tracks || [])
    .map((t) => (t.config.speaker || '').trim()).filter(Boolean));
  for (let n = taken.size + 1; ; n += 1) {
    const name = T('patch.speakerN', { n });
    if (!taken.has(name)) return name;
  }
}

/* Kortin nosto. Klikkaus nostaa ja seuraava klikkaus paikkaan laskee — sama
   kuin raahaus, mutta toimii myös näppäimistöllä. */
function pickUp(media) {
  picked = picked === media.key ? null : media.key;
  applyPicked();
}

/* Nostetun kortin ja kelpaavien paikkojen merkintä. Luokat asetetaan suoraan
   eikä taulua piirretä uudestaan: puhujan nimikentässä voi olla kesken
   kirjoitettu arvo. */
function applyPicked() {
  const held = picked ? trackByKey(picked) : null;
  if (picked && !held) picked = null;
  document.querySelectorAll('.card').forEach((el) => {
    el.classList.toggle('is-picked', !!held && el.dataset.track === picked);
  });
  document.querySelectorAll('.cell').forEach((el) => {
    el.classList.toggle('is-target', !!held && el.dataset.accepts === held.kind);
  });
}

/* Yksi kortti: raita siinä paikassa jossa se nyt on. Kuvakortilla on
   pikkukuva, äänikortilla mikin säätimet — eri sisältö, koska sarakkeetkin
   ovat eri. */
function trackCard(media, dest) {
  const card = document.createElement('div');
  card.className = `card card-${media.kind}`;
  card.dataset.track = media.key;
  card.draggable = true;
  card.tabIndex = 0;
  card.title = (media.parts || []).map((p) => p.path || p.name).join('\n')
    || media.path || media.name;

  const head = document.createElement('div');
  head.className = 'card-head';

  if (media.kind === 'video') {
    const thumb = document.createElement('img');
    thumb.className = 'thumb';
    thumb.loading = 'lazy';
    thumb.alt = '';
    thumb.src = `/api/thumb?track=${encodeURIComponent(media.key)}`;
    thumb.addEventListener('error', () => thumb.classList.add('thumb-missing'));
    head.append(thumb);
  } else {
    const glyph = document.createElement('span');
    glyph.className = 'glyph';
    glyph.innerHTML = MIC_ICON;
    head.append(glyph);
  }

  const meta = document.createElement('div');
  meta.className = 'meta';
  const name = document.createElement('div');
  name.className = 'name';
  name.textContent = media.name;
  const tags = document.createElement('div');
  tags.className = 'tags';
  tags.textContent = trackFacts(media);
  meta.append(name, tags);
  head.append(meta);
  card.append(head);

  if (media.config.role === 'mic') {
    const knobs = document.createElement('div');
    knobs.className = 'knobs';
    TRACK_KNOBS().forEach((spec) => {
      knobs.append(knob(spec, media.config[spec.key], (v) => {
        media.config[spec.key] = v;
        schedule();
      }));
    });
    /* Säätimen kahva on kortin sisällä, ja kortti on raahattava. Ilman tätä
       kahvan vetäminen alkaisi raahata koko korttia eikä säädin liikkuisi,
       ja klikkaus nostaisi kortin kesken säätämisen. */
    knobs.addEventListener('mousedown', () => { card.draggable = false; });
    knobs.addEventListener('mouseup', () => { card.draggable = true; });
    knobs.addEventListener('click', (e) => {
      if (e.stopPropagation) e.stopPropagation();
    });
    card.append(knobs);
  }

  if (media.missing) {
    const gone = (media.parts || []).filter((p) => p.missing).map((p) => p.path);
    card.append(Object.assign(document.createElement('div'), {
      className: 'warn',
      textContent: T('app.missingFile', { paths: gone.join(', ') || media.path }),
    }));
  } else if (media.envelope_error) {
    card.append(Object.assign(document.createElement('div'),
      { className: 'warn', textContent: media.envelope_error }));
  }

  card.addEventListener('dragstart', (e) => {
    picked = media.key;
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', media.key);
    }
    card.classList.add('is-dragging');
    applyPicked();
  });
  card.addEventListener('dragend', () => {
    card.classList.remove('is-dragging');
    card.draggable = true;
  });
  /* Klikkaus kortin päällä: jos kädessä on jo toinen samanlainen kortti, se
     lasketaan tähän paikkaan. Muuten tämä kortti nousee käteen. Tapahtuma ei
     saa nousta solulle, joka laskisi kortin heti takaisin samaan paikkaan. */
  card.addEventListener('click', (e) => {
    if (e.stopPropagation) e.stopPropagation();
    const held = trackByKey(picked);
    if (dest && held && held !== media && held.kind === media.kind) {
      assign(held, dest);
      return;
    }
    pickUp(media);
  });
  card.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    if (e.preventDefault) e.preventDefault();
    pickUp(media);
  });
  return card;
}

/* Pudotuksen kuuntelijat. Samat kolme tapahtumaa joka kohteessa, joten ne
   annetaan yhdestä paikasta: kortin voi pudottaa tai klikata paikalleen. */
function dropTarget(el, wants, place) {
  el.addEventListener('dragover', (e) => {
    if (!wants(trackByKey(picked))) return;
    if (e.preventDefault) e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    el.classList.add('is-over');
  });
  el.addEventListener('dragleave', () => el.classList.remove('is-over'));
  el.addEventListener('drop', (e) => {
    if (e.preventDefault) e.preventDefault();
    el.classList.remove('is-over');
    const key = picked || (e.dataTransfer && e.dataTransfer.getData('text/plain'));
    const media = trackByKey(key);
    if (media && wants(media)) place(media);
  });
  el.addEventListener('click', () => {
    const media = trackByKey(picked);
    if (media && wants(media)) place(media);
  });
  return el;
}

/* Yksi solu: paikan toinen puoli. Tyhjä solu kertoo mitä siihen kuuluu, koska
   juuri tyhjä kohta on se jota käyttäjä etsii. */
function slotCell(slot, side, hint) {
  const cell = document.createElement('div');
  cell.className = `cell cell-${side}`;
  cell.dataset.accepts = side;
  const dest = { kind: slot.kind, side, name: slot.name };
  const cards = side === 'video' ? slot.video : slot.audio;

  if (cards.length) {
    cards.forEach((media) => cell.append(trackCard(media, dest)));
  } else {
    cell.classList.add('empty');
    cell.append(Object.assign(document.createElement('span'),
      { className: 'placeholder', textContent: hint }));
  }
  return dropTarget(cell, (m) => accepts(dest, m), (m) => assign(m, dest));
}

/* Paikan keskisarake: väri, nimi kerran ja piuha. Piuha piirtyy vain kun
   molemmat päät ovat olemassa — puuttuva pari on juuri se mitä rivistä pitää
   nähdä lukematta. */
function slotStrip(slot) {
  const strip = document.createElement('div');
  strip.className = 'strip';
  if (slot.video.length && slot.audio.length) strip.classList.add('linked');

  if (slot.kind === 'shared') {
    strip.append(Object.assign(document.createElement('span'),
      { className: 'strip-label', textContent: T('patch.shared') }));
    return strip;
  }

  const field = document.createElement('input');
  field.type = 'text';
  field.className = 'speaker';
  field.placeholder = T('app.speaker');
  field.value = slot.name;
  field.setAttribute('aria-label', T('app.speaker'));
  const members = [...slot.video, ...slot.audio];
  field.addEventListener('input', () => {
    members.forEach((m) => { m.config.speaker = field.value; });
    schedule();
  });
  /* Nimen vahvistus piirtää taulun uusiksi: väri ja paikkojen järjestys
     seuraavat nimeä, eikä rivi saa hypätä kesken kirjoittamisen. */
  field.addEventListener('change', () => { renderLegend(); renderTracks(); });
  strip.append(field);
  return strip;
}

/* Yksi rivi kytkentätaulussa. */
function slotRow(slot) {
  const row = document.createElement('div');
  row.className = `slot slot-${slot.kind}`;
  const color = slot.kind === 'shared' ? css(WIDE_COLOR)
    : slot.name ? colorFor(speakerIndex(slot.name)) : null;
  if (color) {
    row.style.setProperty('--tint', color);
    row.classList.add('is-tinted');
  }
  const shared = slot.kind === 'shared';
  row.append(
    slotCell(slot, 'video', shared ? T('role.wide') : T('role.close')),
    slotStrip(slot),
    slotCell(slot, 'audio', shared ? T('audio.room') : T('role.mic')));
  return row;
}

/* Uuden puhujan paikka. Tyhjä rivi taulun alalaidassa: kortti siihen, ja
   puhuja on olemassa. */
function newSlotRow() {
  const row = document.createElement('div');
  row.className = 'slot slot-new';
  const cellFor = (side) => {
    const cell = document.createElement('div');
    cell.className = `cell cell-${side} empty`;
    cell.dataset.accepts = side;
    cell.append(Object.assign(document.createElement('span'),
      { className: 'placeholder', textContent: T('patch.newSpeaker') }));
    return dropTarget(cell, (m) => !!m && m.kind === side,
      (m) => assign(m, { kind: 'speaker', side, name: newSpeakerName() }));
  };
  row.append(cellFor('video'),
             Object.assign(document.createElement('div'), { className: 'strip' }),
             cellFor('audio'));
  return row;
}

/* Käyttämättömät raidat. Ne ovat taulun alla omana varastonaan eivätkä
   riveinä: paikattomalla raidalla ei ole paria eikä siis riviä. */
function renderTray(tray) {
  const host = $('tray');
  host.textContent = '';
  const head = document.createElement('div');
  head.className = 'tray-head';
  head.append(Object.assign(document.createElement('h3'),
    { className: 'group', textContent: T('patch.tray') }));
  head.append(Object.assign(document.createElement('span'),
    { className: 'muted small', textContent: T('patch.hint') }));
  host.append(head);

  const box = document.createElement('div');
  box.className = 'tray-box';
  box.dataset.accepts = 'any';
  const trayDest = { kind: 'tray', side: 'any', name: '' };
  if (!tray.length) {
    box.classList.add('empty');
    box.append(Object.assign(document.createElement('span'),
      { className: 'placeholder', textContent: T('patch.trayEmpty') }));
  } else {
    tray.forEach((media) => box.append(trackCard(media, trayDest)));
  }
  host.append(dropTarget(box, (m) => !!m, (m) => assign(m, trayDest)));
}

/* Koko taulu. */
function renderTracks() {
  const host = $('patch');
  host.textContent = '';

  const heads = document.createElement('div');
  heads.className = 'slot patch-head';
  heads.append(
    Object.assign(document.createElement('div'),
      { className: 'col-head', textContent: T('app.group.video') }),
    document.createElement('div'),
    Object.assign(document.createElement('div'),
      { className: 'col-head', textContent: T('app.group.audio') }));
  host.append(heads);

  const { slots, tray } = buildSlots();
  slots.forEach((slot) => host.append(slotRow(slot)));
  host.append(newSlotRow());
  renderTray(tray);
  applyPicked();
}

function renderGlobals() {
  const rhythmRules = $('rhythm-rules');
  if (rhythmRules) {
    rhythmRules.textContent = '';
    RHYTHM_PRESETS().forEach(([value, title, hint]) => {
      const label = document.createElement('label');
      const radio = document.createElement('input');
      radio.type = 'radio'; radio.name = 'rhythm'; radio.value = value;
      radio.checked = (state.globals.rhythm || 'broadcast') === value;
      radio.addEventListener('change', () => {
        state.globals.rhythm = value;
        if (PRESET_VALUES[value]) {
          Object.assign(state.globals, PRESET_VALUES[value]);
        }
        renderGlobals();
        schedule(0);
      });
      const text = document.createElement('span');
      text.innerHTML = `${title} <span class="hint">${hint}</span>`;
      label.append(radio, text);
      rhythmRules.append(label);
    });
  }

  const host = $('global-list');
  host.textContent = '';
  GLOBAL_KNOBS().forEach((spec) => {
    host.append(knob(spec, state.globals[spec.key], (v) => {
      state.globals[spec.key] = v;
      if (state.globals.rhythm && PRESET_VALUES[state.globals.rhythm]) {
        if (PRESET_VALUES[state.globals.rhythm][spec.key] !== undefined &&
            Math.abs(PRESET_VALUES[state.globals.rhythm][spec.key] - v) > 1e-4) {
          state.globals.rhythm = 'custom';
          if (rhythmRules) {
            const customRadio = rhythmRules.querySelector('input[value="custom"]');
            if (customRadio) customRadio.checked = true;
          }
        }
      }
      schedule();
    }));
  });


  /* Pitkä puheenvuoro. «Laajan kesto» koskee vain paluusääntöä, joten se
     piilotetaan kun laajaan jäädään — muuten säädin lupaa vaikutusta jota
     sillä ei ole. */
  const longtake = $('longtake-params');
  longtake.textContent = '';
  LONGTAKE_KNOBS().forEach((spec) => {
    if (spec.key === 'wide_hold' && state.globals.long_take_rule === 'stay') return;
    longtake.append(knob(spec, state.globals[spec.key], (v) => {
      const was = !!state.globals[spec.key];
      state.globals[spec.key] = v;
      // Nollan ylitys kytkee säännön päälle tai pois, joten osa säätimistä
      // vaihtaa tilaa. Kesken raahauksen ei piirretä uudestaan, koska kahva
      // menettäisi kohdistuksen.
      if (spec.key === 'wide_every' && was !== !!v) renderGlobals();
      schedule();
    }));
  });

  const longRules = $('longtake-rules');
  longRules.textContent = '';
  LONGTAKE_RULES().forEach(([value, title, hint]) => {
    const label = document.createElement('label');
    const radio = document.createElement('input');
    radio.type = 'radio'; radio.name = 'longtake'; radio.value = value;
    radio.checked = state.globals.long_take_rule === value;
    radio.disabled = !state.globals.wide_every;
    radio.addEventListener('change', () => {
      state.globals.long_take_rule = value;
      renderGlobals();
      schedule(0);
    });
    const text = document.createElement('span');
    text.innerHTML = `${title} <span class="hint">${hint}</span>`;
    label.append(radio, text);
    longRules.append(label);
  });

  const rules = $('overlap-rules');
  rules.textContent = '';
  OVERLAP_RULES().forEach(([value, title, hint]) => {
    const label = document.createElement('label');
    const radio = document.createElement('input');
    radio.type = 'radio'; radio.name = 'overlap'; radio.value = value;
    radio.checked = state.globals.overlap_rule === value;
    radio.addEventListener('change', () => {
      state.globals.overlap_rule = value;
      schedule(0);
    });
    const text = document.createElement('span');
    text.innerHTML = `${title} <span class="hint">${hint}</span>`;
    label.append(radio, text);
    rules.append(label);
  });

  const params = $('overlap-params');
  params.textContent = '';
  OVERLAP_KNOBS().forEach((spec) => {
    params.append(knob(spec, state.globals[spec.key], (v) => {
      state.globals[spec.key] = v;
      schedule();
    }));
  });

  renderAudio();

  host.append(resetButton('globals', T('app.reset')));

  const title = $('project-title');
  title.value = state.globals.project_name;
  title.oninput = () => { state.globals.project_name = title.value; schedule(); };

  /* Säätimet tiedostonimeen. Nimi näkyy heti alla olevalla polkurivillä,
     joten valinnan vaikutus on luettavissa ilman vientiä. Rasti rakennetaan
     tässä eikä HTML:ssä, jotta savutesti pääsee laukaisemaan sen. */
  const tags = $('name-tags');
  tags.textContent = '';
  const label = document.createElement('label');
  label.className = 'check';
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.checked = state.globals.name_tags !== false;
  box.addEventListener('change', () => {
    state.globals.name_tags = box.checked;
    schedule(0);
  });
  label.append(box, Object.assign(document.createElement('span'),
    { textContent: T('app.nameTags') }));
  tags.append(label);
}

/* Äänenkäsittely. Käsittely itsessään on hidas ja tapahtuu erillisestä
   painikkeesta: säätimien liikuttelu ei saa käynnistää minuutteja kestävää
   ajoa. Valmiit tiedostot jäävät levylle, joten vienti käyttää niitä. */
function renderAudio() {
  const host = $('audio-panel');
  host.textContent = '';
  const audio = state.audio;
  const info = state.mix || {};

  const toggle = document.createElement('label');
  toggle.className = 'check';
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.checked = !!audio.enabled;
  box.addEventListener('change', () => {
    audio.enabled = box.checked;
    renderAudio();
    schedule(0);
  });
  toggle.append(box, Object.assign(document.createElement('span'),
    { textContent: T('audio.enable') }));
  host.append(toggle);

  if (!audio.enabled) return;

  /* Liitännäinen on ketjun ensimmäinen vaihe: kohinanpoisto ja restaurointi
     tehdään täällä, koska omassa ketjussa ei ole kohinanvaimennusta.
     Koneella voi olla satoja liitännäisiä, joten kenttä on kirjoitettava eikä
     valikko — datalist ehdottaa nimeltä. */
  const plug = document.createElement('label');
  plug.className = 'field';
  plug.append(Object.assign(document.createElement('span'),
    { textContent: T('audio.plugin') }));
  const plugInput = document.createElement('input');
  plugInput.type = 'text';
  plugInput.placeholder = T('audio.pluginHint');
  plugInput.setAttribute('list', 'plugin-names');
  plugInput.value = pluginName(audio.plugin_path);
  plugInput.addEventListener('change', () => {
    audio.plugin_path = pluginPath(plugInput.value);
    plugInput.value = pluginName(audio.plugin_path);
    /* Säätimet kuuluvat siihen liitännäiseen josta ne luettiin: toisen
       liitännäisen nimet eivät osu mihinkään, ja jos osuvat, ne osuvat
       väärään säätimeen. Palvelin tekee saman tarkistuksen. */
    audio.plugin_params = {};
    renderAudio();
    schedule(0);
  });
  plug.append(plugInput);
  host.append(plug);
  loadPlugins();
  if (audio.plugin_path) renderPluginParams(host, audio);

  /* Liitännäinen käyttää yhtä ydintä ja on 97 % käsittelyn ajasta, joten
     palojen määrä on ainoa säädin joka vaikuttaa kestoon. Yläraja on koneen
     ytimet: useampi pala ei ole nopeampi, vain lyhyempi ja muistisyöpömpi.
     Nolla on automaattinen, ykkönen tarkoittaa yhtenä palana. */
  if (audio.plugin_path) {
    const cores = state.cores || 8;
    host.append(knob({
      key: 'plugin_workers',
      label: T('audio.workers'),
      min: 0,
      max: cores,
      step: 1,
      zero: T('audio.workersAuto', { n: state.workers_auto || 1 }),
    }, audio.plugin_workers || 0, (v) => {
      audio.plugin_workers = v;
      schedule();
    }));
  }

  /* Jakelualustan lukema on määrittely eikä makuasia: YouTube normalisoi
     -14:ään, Spotify ja Apple -16:een. Valikko nimeää ne, säädin jää
     vapaaksi, koska kaikki jakelu ei ole näitä. */
  const targets = state.loudness_targets || {};
  if (Object.keys(targets).length) {
    const field = document.createElement('label');
    field.className = 'field';
    field.append(Object.assign(document.createElement('span'),
      { textContent: T('audio.targetPreset') }));
    const select = document.createElement('select');
    Object.entries(targets).forEach(([name, value]) => {
      const opt = document.createElement('option');
      opt.value = String(value);
      opt.textContent = `${T(`audio.target.${name}`)} (${value} LUFS)`;
      if (Math.abs(value - audio.target_lufs) < 0.05) opt.selected = true;
      select.append(opt);
    });
    const custom = document.createElement('option');
    custom.value = '';
    custom.textContent = T('audio.targetCustom');
    if (!Object.values(targets).some((v) => Math.abs(v - audio.target_lufs) < 0.05)) {
      custom.selected = true;
    }
    select.append(custom);
    select.addEventListener('change', () => {
      if (!select.value) return;      // «mukautettu» ei muuta mitään
      audio.target_lufs = Number(select.value);
      renderAudio();
      schedule(0);
    });
    field.append(select);
    host.append(field);
  }

  AUDIO_KNOBS().forEach((spec) => {
    host.append(knob(spec, audio[spec.key], (v) => {
      audio[spec.key] = v;
      schedule();
    }));
  });

  /* Tavoitetaso koskee ohjelmaa eikä yhtä stemiä: kaksi tavoitteeseen
     normalisoitua mikkiä summautuu sen yli. Ruutu on heti tavoitetason
     perässä, koska se kertoo mitä tavoite tarkoittaa. */
  const program = document.createElement('label');
  program.className = 'check';
  const programBox = document.createElement('input');
  programBox.type = 'checkbox';
  programBox.checked = !!audio.program_target;
  programBox.addEventListener('change', () => {
    audio.program_target = programBox.checked;
    renderAudio();
    schedule(0);
  });
  program.append(programBox, Object.assign(document.createElement('span'),
    { textContent: T('audio.programTarget') }));
  host.append(program);

  const ess = document.createElement('label');
  ess.className = 'check';
  const essBox = document.createElement('input');
  essBox.type = 'checkbox';
  essBox.checked = !!audio.declick;
  essBox.addEventListener('change', () => {
    audio.declick = essBox.checked;
    renderAudio();
    schedule();
  });
  ess.append(essBox, Object.assign(document.createElement('span'),
    { textContent: T('audio.declick') }));
  host.append(ess);
  if (audio.declick) {
    DECLICK_KNOBS().forEach((spec) => {
      host.append(knob(spec, audio[spec.key], (v) => {
        audio[spec.key] = v;
        schedule();
      }));
    });
  }

  /* Toisen mikin vaimennus. Ohjaus tulee samasta puheentunnistuksesta kuin
     esikatselupalkin värit, joten palkki kertoo suoraan milloin kumpikin
     mikki on auki. */
  const duckBox = document.createElement('input');
  duckBox.type = 'checkbox';
  duckBox.checked = !!audio.duck;
  duckBox.addEventListener('change', () => {
    audio.duck = duckBox.checked;
    renderAudio();
    schedule(0);
  });
  const duckLabel = document.createElement('label');
  duckLabel.className = 'check';
  duckLabel.append(duckBox, Object.assign(document.createElement('span'),
    { textContent: T('audio.duck') }));
  host.append(duckLabel);
  if (audio.duck) {
    DUCK_KNOBS().forEach((spec) => {
      host.append(knob(spec, audio[spec.key], (v) => {
        audio[spec.key] = v;
        schedule();
      }));
    });
  }

  /* Tilaääni: kameran oma mikki matalalla omalla roolillaan. Valittavana ovat
     vain raidat joissa on ääntä. */
  const field = document.createElement('label');
  field.className = 'field';
  field.append(Object.assign(document.createElement('span'),
    { textContent: T('audio.room') }));
  const select = document.createElement('select');
  const none = document.createElement('option');
  none.value = ''; none.textContent = T('audio.roomOff');
  select.append(none);
  state.tracks.filter((t) => t.has_audio && t.has_video).forEach((t) => {
    const opt = document.createElement('option');
    opt.value = t.key; opt.textContent = t.name;
    if (audio.room_track === t.key) opt.selected = true;
    select.append(opt);
  });
  select.addEventListener('change', () => {
    audio.room_track = select.value;
    renderAudio();
    schedule(0);
  });
  field.append(select);
  host.append(field);

  if (audio.room_track) {
    ROOM_KNOBS().forEach((spec) => {
      host.append(knob(spec, audio[spec.key], (v) => {
        audio[spec.key] = v;
        schedule();
      }));
    });
  }

  host.append(mixButton(info));

  host.append(resetButton('audio', T('app.reset.audio')));

  const busy = !!(info.progress && info.progress.running);
  const note = document.createElement('p');
  note.className = 'muted small';
  if (busy) {
    const p = info.progress;
    /* Liitännäinen voi olla hidas — dxRevive noin 7x reaaliaika — joten
       pelkkä 2/4 ei riitä kertomaan paljonko vielä menee. Palkki kertoo
       painotetun osuuden, vaiheen nimi sen miksi se liikkuu hitaasti. */
    host.append(progressBar(p.fraction));
    const eta = p.eta ? T('audio.left', { time: fmtLeft(p.eta) }) : '';
    const stage = p.stage ? ' · ' + T(`audio.stage.${p.stage}`) : '';
    note.textContent = `${p.done}/${p.total}`
      + (p.current ? ' · ' + p.current : '') + stage + eta;
  } else if (info.errors && info.errors.length) {
    note.className = 'warn';
    note.textContent = info.errors.join('\n');
  } else if (info.ready) {
    /* Nosto näkyviin: se nostaa myös pohjakohinaa, eikä sitä saa tehdä
       huomaamatta. +26 dB kertoo enemmän kuin "valmis". */
    const gains = Object.values(info.gains || {});
    const lift = gains.length
      ? T('audio.readyGain', { low: Math.min(...gains).toFixed(1),
                               high: Math.max(...gains).toFixed(1) })
      : '';
    /* Mitattu trimmi näkyviin: ilman sitä stemi mittaa tavoitteen alle ja
       se näyttää virheeltä. */
    const trim = info.program_trim
      ? T('audio.readyProgram', { db: info.program_trim.toFixed(1) })
      : '';
    /* Vanhentuneet erikseen: «4 valmiina» ja «4 valmiina, 2 niistä eri
       asetuksilla» ovat eri tilanteita, ja jälkimmäinen on se jossa
       painiketta kannattaa painaa. */
    const stale = Math.max(0, (info.expected || 0) - (info.fresh || 0));
    note.textContent = T('audio.ready', { n: info.ready })
      + (info.room ? T('audio.readyRoom', { n: info.room }) : '') + lift + trim
      + (stale ? T('audio.readyStale', { n: stale }) : T('audio.readyTail'));
  } else {
    note.textContent = T('audio.idle');
  }
  host.append(note);
}

/* Käsittelypainike, joka muistaa mitä on jo tehty.

   Kolme tilaa, jotka näyttivät ennen samalta. Ajossa: palkki ja «Käsitellään».
   Jotain tekemättä: «Käsittele ääni». Kaikki ajan tasalla: painike kertoo sen
   eikä kutsu painamaan — ja jos silti painetaan, se kysyy ensin. Ajo maksaa
   minuutteja, eikä sitä saa aloittaa vahingossa siksi että painike näytti
   samalta kuin ennen työtä.

   Oma funktionsa siksi, että `send()` vaihtaa tämän yksin: koko paneelin
   piirtäminen uudestaan kesken liu'un vaihtaisi raahattavan säätimen alta. */
function mixButton(info) {
  const busy = !!(info.progress && info.progress.running);
  const expected = info.expected || 0;
  const done = expected > 0 && (info.fresh || 0) >= expected;

  const wrap = document.createElement('span');
  wrap.id = 'mix-run';
  wrap.className = 'mix-run';

  const run = document.createElement('button');
  run.className = 'ghost';
  if (busy) {
    run.textContent = T('audio.run');
    setBusy(run, true, T('audio.running'));
    wrap.append(run);
    return wrap;
  }
  if (done && !mixConfirm) {
    run.textContent = T('audio.runDone', { n: expected });
    run.classList.add('done');
    run.addEventListener('click', () => { mixConfirm = true; swapMixButton(); });
    wrap.append(run);
    return wrap;
  }
  if (done && mixConfirm) {
    run.textContent = T('audio.runAgain');
    run.classList.add('warn');
    run.addEventListener('click', () => { mixConfirm = false; runMix(true); });
    const cancel = document.createElement('button');
    cancel.className = 'ghost';
    cancel.textContent = T('app.cancel');
    cancel.addEventListener('click', () => { mixConfirm = false; swapMixButton(); });
    wrap.append(run, cancel);
    return wrap;
  }
  run.textContent = T('audio.run');
  run.addEventListener('click', () => runMix(false));
  wrap.append(run);
  return wrap;
}

/* Vaihtaa pelkän painikkeen paikallaan. Ks. mixButton: paneelia ei piirretä
   uudestaan, koska säätimiä saatetaan juuri raahata. */
function swapMixButton() {
  const old = $('mix-run');
  if (old && state && state.mix) old.replaceWith(mixButton(state.mix));
}

/* Edistymispalkki. Osuus on painotettu tiedostokoolla ja vaiheella, joten se
   liikkuu myös tunnin mittaisen tiedoston aikana. Ilman osuutta palkki on
   määrittelemättömässä tilassa — silloin liike kertoo vain että jokin käy,
   mikä on silti enemmän kuin liikkumaton palkki. */
function progressBar(fraction) {
  const wrap = document.createElement('div');
  wrap.className = 'progress';
  const fill = document.createElement('div');
  if (typeof fraction === 'number' && fraction >= 0) {
    fill.className = 'progress-fill';
    fill.style.width = `${Math.min(100, Math.max(1, fraction * 100))}%`;
  } else {
    fill.className = 'progress-fill indeterminate';
  }
  wrap.append(fill);
  return wrap;
}

/* Paluu tehdasasetuksiin. Säätimiä on kolmisenkymmentä ja ne periytyvät
   seuraavaan jaksoon, joten yhdestä huonosta arvosta ei muuten pääse
   takaisin. Roolit ja puhujat jäävät: ne ovat työtä, eivät säätöä. */
async function resetSection(which) {
  const data = await (await fetch('/api/defaults')).json();
  if (which === 'globals') {
    const name = state.globals.project_name;
    state.globals = data.globals;
    state.globals.project_name = name;
    renderGlobals();
  } else {
    const keep = { room_track: state.audio.room_track,
                   plugin_path: state.audio.plugin_path,
                   plugin_params: state.audio.plugin_params,
                   enabled: state.audio.enabled };
    state.audio = Object.assign(data.audio, keep);
    renderAudio();
  }
  schedule(0);
}

function resetButton(which, title) {
  const button = document.createElement('button');
  button.className = 'ghost small';
  button.type = 'button';
  button.textContent = title;
  button.addEventListener('click', () => resetSection(which));
  return button;
}

/* Liitännäisluettelo haetaan kerran; se on sama koko koneella. */
async function loadPlugins() {
  if (pluginList) return;
  try {
    pluginList = (await (await fetch('/api/plugins')).json()).plugins || [];
  } catch (err) {
    pluginList = [];
    return;
  }
  $('plugin-names').innerHTML = pluginList
    .map((p) => `<option value="${p.name.replace(/"/g, '&quot;')}">`).join('');
}

function pluginName(path) {
  if (!path) return '';
  const hit = (pluginList || []).find((p) => p.path === path);
  return hit ? hit.name : path.split('/').pop().replace(/\.(vst3|component)$/, '');
}

function pluginPath(name) {
  const wanted = name.trim();
  if (!wanted) return '';
  const hit = (pluginList || []).find((p) => p.name === wanted);
  return hit ? hit.path : '';
}

/* Liitännäisen omat säätimet. Luettelo tulee palvelimelta, koska se joutuu
   lataamaan liitännäisen — sekunteja, eikä sitä tehdä kaikille 800:lle.
   Vastaus jää polun mukaan talteen: liitännäinen ei muutu ajon aikana. */
async function loadPluginParams(path) {
  if (!path || pluginParams[path]) return;
  pluginParams[path] = 'loading';
  try {
    const response = await fetch('/api/plugin-params?path='
                                 + encodeURIComponent(path));
    const data = await response.json();
    pluginParams[path] = (response.ok && data && data.params) ? data : 'error';
  } catch (err) {
    pluginParams[path] = 'error';
  }
  renderAudio();
}

/* Säätimet ruudulle. Arvo on liitännäisen omassa yksikössä — desibeliä,
   prosenttia, hertsiä sen mukaan mitä liitännäinen sanoo — eikä 0–1-raakana,
   koska raaka-arvon ja näkyvän luvun välinen muunnos ei ole aina lineaarinen
   ja pedalboard osaa sen itse.

   Asetuksiin kirjoitetaan vain koskettu säädin. Koskematon jää liitännäisen
   omaan oletukseen, jolloin asetustiedostoon ei jää kymmeniä rivejä
   arvoja jotka ovat oletuksia joka tapauksessa. */
function renderPluginParams(host, audio) {
  const cached = pluginParams[audio.plugin_path];
  if (!cached || cached === 'loading') {
    loadPluginParams(audio.plugin_path);
    host.append(Object.assign(document.createElement('p'),
      { className: 'muted small', textContent: T('audio.pluginLoading') }));
    return;
  }
  /* h3 kuten muutkin osion otsikot: tyyli on jo olemassa eikä tähän
     tarvita uutta. */
  const head = document.createElement('h3');
  head.textContent = T('audio.pluginParams');
  host.append(head);
  if (cached === 'error') {
    host.append(Object.assign(document.createElement('p'),
      { className: 'warn', textContent: T('audio.pluginFailed') }));
    return;
  }
  const specs = cached.params || [];
  if (!specs.length) {
    host.append(Object.assign(document.createElement('p'),
      { className: 'muted small', textContent: T('audio.pluginNoParams') }));
    return;
  }
  const values = audio.plugin_params || (audio.plugin_params = {});
  const set = (name, value) => { values[name] = value; schedule(); };
  specs.forEach((spec) => {
    const value = (spec.name in values) ? values[spec.name] : spec.value;
    if (spec.type === 'bool') {
      const label = document.createElement('label');
      label.className = 'check';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = !!value;
      box.addEventListener('change', () => set(spec.name, box.checked));
      label.append(box, Object.assign(document.createElement('span'),
        { textContent: spec.label }));
      host.append(label);
    } else if (spec.type === 'choice') {
      const field = document.createElement('label');
      field.className = 'field';
      field.append(Object.assign(document.createElement('span'),
        { textContent: spec.label }));
      const select = document.createElement('select');
      (spec.choices || []).forEach((choice) => {
        const opt = document.createElement('option');
        opt.value = choice; opt.textContent = choice;
        if (choice === value) opt.selected = true;
        select.append(opt);
      });
      select.addEventListener('change', () => set(spec.name, select.value));
      field.append(select);
      host.append(field);
    } else {
      host.append(knob({ key: spec.name, label: spec.label, min: spec.min,
                         max: spec.max, step: spec.step,
                         unit: spec.units ? ' ' + spec.units : '' },
                       Number(value), (v) => set(spec.name, v)));
    }
  });
  /* Katkaisu sanotaan ääneen: hiljainen katkaisu näyttäisi siltä ettei
     liitännäisessä ole enempää säädettävää. */
  if (cached.total > specs.length) {
    host.append(Object.assign(document.createElement('p'),
      { className: 'muted small',
        textContent: T('audio.pluginMore', { n: cached.total - specs.length }) }));
  }
  const defaults = document.createElement('button');
  defaults.className = 'ghost small';
  defaults.type = 'button';
  defaults.textContent = T('audio.pluginDefaults');
  defaults.addEventListener('click', () => {
    audio.plugin_params = {};
    renderAudio();
    schedule(0);
  });
  host.append(defaults);
}

/* Jäljellä oleva aika lyhyesti: minuutit riittävät, sekunnit eivät auta. */
function fmtLeft(seconds) {
  if (seconds < 90) return T('unit.sec', { n: Math.round(seconds) });
  return T('unit.min', { n: Math.round(seconds / 60) });
}

/* Käsittelyn käynnistys ja edistymisen seuranta. */
async function runMix(force) {
  try {
    const response = await fetch('/api/mix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload(), force: !!force }),
    });
    const data = await response.json();
    if (!response.ok) {
      banner(data.detail || T('audio.startFailed'), true);
      return;
    }
    banner('');
    watchMix(true);
  } catch (err) {
    banner(T('audio.failed', { error: err.message }), true);
  }
}

/* Käsittely on palvelimen taustasäie, ei selaimen. Sivun lataus tai
   uudelleenluku kesken ajon jättäisi palkin paikalleen, joten seuranta
   käynnistetään myös silloin kun se oli jo käynnissä ennen tuloa. */
function watchMixIfRunning() {
  if (state.mix && state.mix.progress && state.mix.progress.running) watchMix();
}

function watchMix(announce) {
  clearInterval(mixTimer);
  mixTimer = setInterval(async () => {
    const data = await (await fetch('/api/state')).json();
    state.mix = data.mix;
    renderAudio();
    if (data.mix.progress.running) return;
    clearInterval(mixTimer);
    /* Ajo jolla ei ollut mitään tehtävää on ohi ennen ensimmäistä kyselyä:
       palkki ei ehdi näkyä eikä teksti muutu. Ilman tätä painike näyttää
       siltä ettei se tee mitään. */
    if (!announce) return;
    if (data.mix.errors && data.mix.errors.length) return;
    /* `processed` on tämän ajon luku: `run_mix` korvaa koko tuloksen. */
    const done = data.mix.processed || 0;
    banner(done > 0 ? T('audio.done', { n: done }) : T('audio.nothingToDo'));
  }, 700);
}

/* ------------------------------------------------------------ esikatselu */

function colorFor(index) {
  return index < 0 ? css(WIDE_COLOR) : css(SPEAKER_COLORS[index % SPEAKER_COLORS.length]);
}

/* Esikatselupalkki: rivi per puhuja (kuka on äänessä) ja alimpana valittu kuva.
   Piirretään devicePixelRatiolla, jotta viivat eivät sumene Retinalla. */
function drawBar() {
  const canvas = $('bar');
  const preview = latest && latest.preview;
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const rows = preview ? preview.speakers.length + 1 : 1;
  const rowHeight = 22, gap = 4;
  const height = rows * rowHeight + (rows - 1) * gap + 8;
  canvas.style.height = height + 'px';
  canvas.width = Math.max(1, Math.round(width * ratio));
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  if (!preview || !width) return;

  const columns = preview.columns;
  const step = width / columns;

  preview.speakers.forEach((sp, row) => {
    const y = 4 + row * (rowHeight + gap);
    ctx.fillStyle = '#191c22';
    ctx.fillRect(0, y, width, rowHeight);
    ctx.fillStyle = colorFor(sp.index);
    for (let i = 0; i < columns; i++) {
      if (sp.active[i]) ctx.fillRect(i * step, y, Math.max(step, 1), rowHeight);
    }
  });

  const y = 4 + preview.speakers.length * (rowHeight + gap);
  ctx.fillStyle = '#191c22';
  ctx.fillRect(0, y, width, rowHeight);
  for (let i = 0; i < columns; i++) {
    const value = preview.chosen[i];
    ctx.fillStyle = colorFor(value === preview.wide_value ? -1 : value);
    ctx.fillRect(i * step, y, Math.max(step, 1), rowHeight);
  }
  // Leikkausrajat ohuina viivoina, jotta tiheys näkyy myös silmällä.
  ctx.fillStyle = 'rgba(0,0,0,.55)';
  for (let i = 1; i < columns; i++) {
    if (preview.chosen[i] !== preview.chosen[i - 1]) ctx.fillRect(i * step, y, 1, rowHeight);
  }
}

/* Aika-asteikko palkin alle. Väli valitaan luettavista arvoista (1, 2, 5, 10,
   15, 30 s, 1, 2, 5 min …) niin että merkkejä tulee noin kahdeksan. */
function renderRuler() {
  const host = $('ruler');
  host.textContent = '';
  if (!latest || !latest.program) return;
  const total = latest.program.duration;
  const targets = 8;
  const raw = total / targets;
  const nice = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]
    .find((v) => v >= raw) || 3600;
  for (let t = 0; t <= total + 0.001; t += nice) {
    const span = document.createElement('span');
    span.style.left = `${(t / total) * 100}%`;
    span.textContent = fmtTime(t);
    host.append(span);
  }
}

function renderLegend() {
  const host = $('legend');
  host.textContent = '';
  if (!latest || !latest.preview) return;
  const entries = latest.preview.speakers.map((s) => [colorFor(s.index),
    s.has_close ? s.name : T('legend.noClose', { name: s.name })]);
  entries.push([colorFor(-1), T('legend.wide')]);
  entries.forEach(([color, text]) => {
    const item = document.createElement('span');
    item.innerHTML = `<i style="background:${color}"></i>${text}`;
    host.append(item);
  });
}

/* Kuvan nimi näytölle. Puhujan nimi on käyttäjän kirjoittama ja kelpaa
   sellaisenaan, mutta laajan tunnus on aineistoa: sama merkkijono menee
   vientiin rooliksi, joten sitä ei käännetä siellä missä se syntyy vaan
   tässä. Palvelin kertoo mikä tunnus on, jottei sitä tarvitse kirjoittaa
   kahteen kertaan. */
function shotLabel(label) {
  return (latest && label === latest.wide_label) ? T('legend.wide') : label;
}

function renderCuts() {
  const body = document.querySelector('#cut-table tbody');
  body.textContent = '';
  if (!latest || !latest.ok) { $('cut-summary').textContent = ''; return; }
  const start = latest.program.start;
  latest.segments.forEach((seg, i) => {
    const tr = document.createElement('tr');
    /* Laaja ei ole puhuja, joten sitä ei löydy puhujalistasta: ilman tätä
       speakerIndex putoaisi nollaan ja laaja saisi ensimmäisen puhujan
       värin — eri värin kuin sama laaja selitteessä ja palkissa. */
    const index = seg.label === latest.wide_label ? -1 : speakerIndex(seg.label);
    tr.innerHTML =
      `<td>${i + 1}</td>` +
      `<td>${fmtTime(seg.start - start)}</td>` +
      `<td>${fmtTime(seg.end - start)}</td>` +
      `<td>${seg.duration.toFixed(2)} s</td>` +
      `<td><span class="swatch" style="background:${colorFor(index)}"></span>`
      + `${shotLabel(seg.label)}</td>`;
    body.append(tr);
  });
  const counts = Object.entries(latest.counts)
    .map(([k, v]) => `${shotLabel(k)} ${v}`).join(' · ');
  $('cut-summary').textContent = [T('app.shots', { n: latest.segments.length }),
                                  fmtTime(latest.program.duration),
                                  counts].filter(Boolean).join(' · ');
  $('counts').textContent = T('app.decision', { ms: latest.ms });
}

/* ------------------------------------------------------------ liikenne */

/* Käyttöliittymän koko tila palvelimelle. Sama rakenne kelpaa sekä säätöön että
   vientiin, joten vienti käyttää varmasti sitä mitä ruudulla näkyy. */
function payload() {
  const tracks = {};
  state.tracks.forEach((m) => { tracks[m.key] = m.config; });
  return { tracks, globals: state.globals, audio: state.audio };
}

/* Niputus: säätimen liike ei lähetä pyyntöä heti. Roolin vaihto kutsutaan
   viiveellä 0, koska se on kertaklikkaus eikä raahaus. */
function schedule(delay = DEBOUNCE_MS) {
  clearTimeout(pending);
  pending = setTimeout(send, delay);
}

/* Päätöskierros. Edellinen pyyntö keskeytetään, jotta raahaus ei kasaa jonoa
   eikä vanhentunut vastaus ehdi ylikirjoittaa tuoretta. */
async function send() {
  if (!state) return;
  if (inflight) inflight.abort();
  inflight = new AbortController();
  try {
    const response = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload()),
      signal: inflight.signal,
    });
    const data = await response.json();
    latest = data;
    if (data.output_path && data.output_path !== state.output_path) {
      state.output_path = data.output_path;
      renderHeader();
    }
    /* Asetuksen muutos vanhentaa valmiin työn. Vain painike vaihdetaan:
       koko paneelin piirtäminen veisi raahattavan säätimen alta. */
    if (data.mix_fresh && state.mix
        && (state.mix.fresh !== data.mix_fresh.fresh
            || state.mix.expected !== data.mix_fresh.expected)) {
      state.mix.fresh = data.mix_fresh.fresh;
      state.mix.expected = data.mix_fresh.expected;
      mixConfirm = false;
      swapMixButton();
    }
    if (data.ok) {
      banner('');
      drawBar(); renderRuler(); renderLegend(); renderCuts();
    } else {
      banner((data.problems || [T('app.unknownError')]).join('\n'));
      latest = { ...data, preview: null };
      drawBar(); renderCuts();
    }
  } catch (err) {
    if (err.name !== 'AbortError') banner(T('app.noServer', { error: err.message }), true);
  } finally {
    inflight = null;
  }
}

/* Painike työn ajaksi kehruuseen. Alkuperäinen teksti talletetaan elementtiin,
   jotta palautus ei riipu kutsupaikan muistista. */
function setBusy(button, on, label) {
  if (!button) return;
  if (on) {
    if (button.dataset.idleLabel === undefined) {
      button.dataset.idleLabel = button.textContent;
    }
    button.disabled = true;
    button.classList.add('busy');
    if (label) button.textContent = label;
  } else {
    button.disabled = false;
    button.classList.remove('busy');
    if (button.dataset.idleLabel !== undefined) {
      button.textContent = button.dataset.idleLabel;
      delete button.dataset.idleLabel;
    }
  }
}

function banner(text, isError) {
  const el = $('banner');
  el.textContent = text;
  el.classList.toggle('hidden', !text);
  el.classList.toggle('error', !!isError);
}

async function exportXml() {
  const button = $('export');
  if (button.disabled) return;
  setBusy(button, true, T('app.exporting'));
  try {
    const response = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload()),
    });
    const data = await response.json();
    if (response.ok && data.ok) {
      const mixed = data.mixed ? T('app.exportedMix', { n: data.mixed }) : '';
      $('status').textContent =
        T('app.exported', { cuts: data.cuts, file: data.path.split('/').pop() }) + mixed;
      /* Polkurivi näyttää mihin *seuraava* vienti menee: äsken kirjoitettua
         tiedostoa ei enää korvata. */
      if (data.next_path) { state.output_path = data.next_path; renderHeader(); }
      banner((data.warnings || []).join('\n'));
    } else {
      $('status').textContent = '';
      banner((data.problems || [data.detail || T('app.exportFailed', { error: '' })]).join('\n'), true);
    }
  } catch (err) {
    $('status').textContent = '';
    banner(T('app.exportFailed', { error: err.message }), true);
  } finally {
    setBusy(button, false);
  }
}

/* ------------------------------------------------------------ käynnistys */

function renderHeader() {
  $('project-name').textContent = state.name || '—';
  const bits = [T(`kind.${state.kind}`), T('meta.fps', { fps: state.fps ?? '?' }),
                T('meta.tracks', { n: state.tracks.length })];
  if (state.parts > 1) bits.push(T('meta.parts', { n: state.parts }));
  $('project-meta').textContent = bits.join(' · ');
  /* Polut omille riveilleen: yhdellä rivillä ne kietoutuvat toisiinsa eikä
     kumpaakaan pysty lukemaan. */
  const paths = $('paths');
  paths.textContent = '';
  const lines = [[T('path.export'), state.output_path],
                 [T('path.settings'), state.settings_path]];
  if (state.inherited_from) {
    lines.push([T('path.inherited'), state.inherited_from]);
  }
  lines.forEach(([title, value]) => {
    const row = document.createElement('div');
    row.className = 'path';
    row.innerHTML = `<b>${title}</b> `;
    row.append(document.createTextNode(value));
    paths.append(row);
  });
}

/* Verhokäyrien edistyminen. Roolit saa nimetä laskennan aikana; kun se
   valmistuu, ajetaan päätös kerran automaattisesti. */
function watchProgress() {
  clearInterval(progressTimer);
  if (state.progress && state.progress.ready) {
    $('status').textContent = '';
    setBusy($('reload'), false);
    return;
  }
  progressTimer = setInterval(async () => {
    const data = await (await fetch('/api/state')).json();
    state.progress = data.progress;
    state.tracks.forEach((m) => {
      const fresh = data.tracks.find((x) => x.key === m.key);
      if (fresh) m.envelope_error = fresh.envelope_error;
    });
    const p = data.progress;
    if (p.ready) {
      clearInterval(progressTimer);
      $('status').textContent = '';
      setBusy($('reload'), false);
      renderTracks();
      send();
    } else {
      $('status').textContent = T('app.envelopes', { done: p.done, total: p.total })
        + (p.current ? ' · ' + p.current : '');
    }
  }, 400);
}

/* Kielivalitsin otsikkoon. Kieli tallentuu asetuksiin ja periytyy jaksosta
   toiseen, joten se valitaan kerran. */
function renderLanguage() {
  const host = $('language');
  host.textContent = '';
  (state.languages || ['fi']).forEach((code) => {
    const button = document.createElement('button');
    button.className = 'ghost lang';
    button.type = 'button';
    button.textContent = code.toUpperCase();
    button.disabled = code === state.language;
    button.addEventListener('click', async () => {
      const data = await (await fetch('/api/language', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language: code }),
      })).json();
      state.language = data.language;
      setLang(state.language);
      redrawAll();
    });
    host.append(button);
  });
}

/* Kaikki tekstit uusiksi. Piirto on halpaa eikä kieltä vaihdeta usein. */
function redrawAll() {
  renderStatic();
  renderLanguage();
  renderHeader();
  renderTracks();
  renderGlobals();
  renderLegend();
  renderCuts();
}

/* Sivun kiinteät otsikot, jotka eivät synny piirtofunktioissa. */
function renderStatic() {
  document.querySelectorAll('[data-t]').forEach((el) => {
    el.textContent = T(el.dataset.t);
  });
  $('open').innerHTML = `${T('app.open')} <kbd>⌘O</kbd>`;
  $('reload').textContent = T('app.reload');
  $('export').innerHTML = `${T('app.export')} <kbd>⌘E</kbd>`;
}

/* Tiedoston avaus. Valitsin on kahdessa paikassa, koska selaimessa ei ole
   kolmatta: natiivi-ikkunassa sen antaa pywebview, selaimessa palvelin avaa
   Finderin puolestamme. Ilman jälkimmäistä nappi ei tee selaimessa mitään
   eikä kerro miksi. */
async function openXml(path) {
  if (!path && typeof window !== 'undefined' && window.pywebview && window.pywebview.api) {
    try {
      path = await window.pywebview.api.open_file_dialog();
    } catch (err) {
      console.error(err);
    }
  }
  if (!path) {
    try {
      const res = await fetch('/api/pick', { method: 'POST' });
      const data = await res.json();
      if (data.unavailable) { banner(T('app.noPicker'), true); return; }
      path = data.path || '';
    } catch (err) {
      banner(T('app.readFailed', { error: err.message }), true);
      return;
    }
    if (!path) return;          // käyttäjä perui
  }
  if (!path) return;
  const button = $('open');
  setBusy(button, true, T('app.reloading'));
  try {
    const res = await fetch('/api/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!res.ok) {
      setBusy(button, false);
      banner(data.detail || T('app.readFailed', { error: 'Open failed' }), true);
      return;
    }
    state = data;
  } catch (err) {
    setBusy(button, false);
    banner(T('app.readFailed', { error: err.message }), true);
    return;
  }
  latest = null;
  setBusy(button, false);
  if (state.error) {
    banner(state.error, true);
    return;
  }
  banner('');
  renderHeader(); renderTracks(); renderGlobals();
  watchProgress();
  watchMixIfRunning();
  if (state.progress && state.progress.ready) send();
}

async function boot() {
  state = await (await fetch('/api/state')).json();
  setLang(state.language || 'fi');
  renderStatic();
  renderLanguage();
  if (state.error) { banner(state.error, true); return; }
  renderHeader();
  renderTracks();
  renderGlobals();
  watchProgress();
  watchMixIfRunning();
  if (state.progress && state.progress.ready) send();
}

$('open').addEventListener('click', () => openXml());
$('export').addEventListener('click', exportXml);
/* Lukeminen jatkuu verhokäyrien laskentana taustalla, joten painike vapautuu
   vasta kun se on ohi — ei kun pyyntö palaa. Muuten nappi näyttäisi
   valmiilta samalla kun tilarivi laskee vielä käyriä, ja uusi klikkaus
   aloittaisi saman työn alusta. */
$('reload').addEventListener('click', async () => {
  const button = $('reload');
  if (button.disabled) return;
  setBusy(button, true, T('app.reloading'));
  try {
    state = await (await fetch('/api/reload', { method: 'POST' })).json();
  } catch (err) {
    setBusy(button, false);
    banner(T('app.readFailed', { error: err.message }), true);
    return;
  }
  latest = null;
  if (state.error) {
    setBusy(button, false);
    banner(state.error, true);
    return;
  }
  banner('');
  renderHeader(); renderTracks(); renderGlobals();
  watchProgress();
  watchMixIfRunning();
  if (state.progress && state.progress.ready) setBusy(button, false);
});
window.addEventListener('resize', () => { drawBar(); renderRuler(); });
window.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'e') {
    e.preventDefault();
    exportXml();
  } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'o') {
    e.preventDefault();
    openXml();
  }
});
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('dragenter', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
    const file = e.dataTransfer.files[0];
    const path = file.path || file.name;
    if (path) openXml(path);
  }
});

boot();
