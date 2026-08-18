'use strict';

/* Käyttöliittymän koko logiikka. Säätimen liike lähettää asetukset
   palvelimelle, joka ajaa vain päätöskerroksen ja palauttaa leikkauslistan ja
   esikatselun. Pyynnöt niputetaan ja edellinen keskeytetään, jotta nopea
   raahaus ei kasaa jonoa. */

const SPEAKER_COLORS = ['--sp0', '--sp1', '--sp2', '--sp3', '--sp4'];
const WIDE_COLOR = '--wide';
const DEBOUNCE_MS = 45;

const ROLE_LABELS = [
  ['unused', 'Ei käytössä'],
  ['wide', 'Laaja'],
  ['close', 'Lähikuva'],
  ['mic', 'Mikki'],
];

const TRACK_KNOBS = [
  { key: 'sensitivity_db', label: 'Herkkyys', min: 0, max: 40, step: 0.5,
    unit: ' dB yli pohjan' },
  { key: 'gain_db', label: 'Vahvistus', min: -24, max: 24, step: 0.5, unit: ' dB' },
];

const GLOBAL_KNOBS = [
  { key: 'min_shot', label: 'Lyhin kuvan kesto', min: 0.3, max: 12, step: 0.1, unit: ' s' },
  { key: 'lead', label: 'Ennakko', min: 0, max: 1.5, step: 0.01, unit: ' s' },
  { key: 'confirm', label: 'Vahvistusaika', min: 0, max: 2, step: 0.02, unit: ' s' },
  { key: 'wide_every', label: 'Laaja pakotetaan', min: 0, max: 90, step: 1, unit: ' s',
    zero: 'ei koskaan' },
];

const OVERLAP_KNOBS = [
  { key: 'min_overlap', label: 'Lyhin päällekkäisyys', min: 0, max: 3, step: 0.05, unit: ' s' },
  { key: 'dominance_db', label: 'Vaadittu ero', min: 0, max: 24, step: 0.5, unit: ' dB' },
];

const OVERLAP_RULES = [
  ['wide', 'Laaja', 'Molemmat äänessä, mennään laajaan.'],
  ['hold', 'Pidä nykyinen', 'Ei leikata mihinkään.'],
  ['louder', 'Vahvempi voittaa', 'Kovempi saa kuvan, kun ero on kestänyt.'],
];

let state = null;               // /api/state
let latest = null;              // viimeisin laskettu tulos
let pending = null;             // ajastin
let inflight = null;            // AbortController
let progressTimer = null;

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

/* ------------------------------------------------------------ raidat */

/* Puhujan indeksi väripaletissa. Tulee palvelimen esikatselusta, jotta palkki,
   selite ja leikkauslista käyttävät varmasti samaa väriä. */
function speakerIndex(name) {
  if (!latest || !latest.preview) return -1;
  const found = latest.preview.speakers.find((s) => s.name === name);
  return found ? found.index : -1;
}

/* Raitalista. Piirretään kokonaan uudestaan roolin vaihtuessa, koska rooli
   määrää mitkä säätimet riviin kuuluvat. Puhujakenttien arvot kerätään
   datalistiin, jotta toisen raidan puhujan voi valita kirjoittamatta. */
function renderTracks() {
  const host = $('track-list');
  host.textContent = '';
  const names = new Set();
  state.media.forEach((m) => { if (m.config.speaker) names.add(m.config.speaker); });
  $('speaker-names').innerHTML = [...names]
    .map((n) => `<option value="${n.replace(/"/g, '&quot;')}">`).join('');

  state.media.forEach((media) => {
    const row = document.createElement('div');
    row.className = 'track';
    if (media.config.role === 'mic') row.classList.add('is-mic');

    const left = document.createElement('div');
    const name = document.createElement('div');
    name.className = 'name';
    name.textContent = media.name;
    name.title = media.path || media.name;
    const tags = document.createElement('div');
    tags.className = 'tags';
    const bits = [];
    if (media.has_video) bits.push(`kuva ${media.width}×${media.height}`);
    if (media.has_audio) bits.push(`ääni ${media.audio_channels} kan.`);
    if (media.fps) bits.push(`${media.fps} fps`);
    if (media.placements > 1) bits.push(`${media.placements} palaa`);
    tags.textContent = bits.join(' · ');
    left.append(name, tags);

    const role = document.createElement('select');
    ROLE_LABELS.forEach(([value, text]) => {
      const opt = document.createElement('option');
      opt.value = value; opt.textContent = text;
      if (media.config.role === value) opt.selected = true;
      role.append(opt);
    });
    role.addEventListener('change', () => {
      media.config.role = role.value;
      renderTracks();
      schedule(0);
    });

    const speaker = document.createElement('input');
    speaker.type = 'text';
    speaker.placeholder = 'Puhuja';
    speaker.setAttribute('list', 'speaker-names');
    speaker.value = media.config.speaker || '';
    speaker.disabled = !(media.config.role === 'close' || media.config.role === 'mic');
    speaker.addEventListener('input', () => {
      media.config.speaker = speaker.value;
      schedule();
    });
    speaker.addEventListener('change', renderLegend);

    row.append(left, role, speaker);

    if (media.config.role === 'mic') {
      const knobs = document.createElement('div');
      knobs.className = 'knobs';
      TRACK_KNOBS.forEach((spec) => {
        knobs.append(knob(spec, media.config[spec.key], (v) => {
          media.config[spec.key] = v;
          schedule();
        }));
      });
      row.append(knobs);
    }

    if (media.missing) {
      const warn = document.createElement('div');
      warn.className = 'warn';
      warn.textContent = 'Tiedostoa ei löydy levyltä: ' + media.path;
      row.append(warn);
    } else if (media.envelope_error) {
      const warn = document.createElement('div');
      warn.className = 'warn';
      warn.textContent = media.envelope_error;
      row.append(warn);
    }

    host.append(row);
  });
}

function renderGlobals() {
  const host = $('global-list');
  host.textContent = '';
  GLOBAL_KNOBS.forEach((spec) => {
    host.append(knob(spec, state.globals[spec.key], (v) => {
      state.globals[spec.key] = v;
      schedule();
    }));
  });

  const rules = $('overlap-rules');
  rules.textContent = '';
  OVERLAP_RULES.forEach(([value, title, hint]) => {
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
  OVERLAP_KNOBS.forEach((spec) => {
    params.append(knob(spec, state.globals[spec.key], (v) => {
      state.globals[spec.key] = v;
      schedule();
    }));
  });

  const title = $('project-title');
  title.value = state.globals.project_name;
  title.oninput = () => { state.globals.project_name = title.value; schedule(); };
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
    s.has_close ? s.name : `${s.name} (ei lähikuvaa)`]);
  entries.push([colorFor(-1), 'Laaja']);
  entries.forEach(([color, text]) => {
    const item = document.createElement('span');
    item.innerHTML = `<i style="background:${color}"></i>${text}`;
    host.append(item);
  });
}

function renderCuts() {
  const body = document.querySelector('#cut-table tbody');
  body.textContent = '';
  if (!latest || !latest.ok) { $('cut-summary').textContent = ''; return; }
  const start = latest.program.start;
  latest.segments.forEach((seg, i) => {
    const tr = document.createElement('tr');
    const index = speakerIndex(seg.label);
    tr.innerHTML =
      `<td>${i + 1}</td>` +
      `<td>${fmtTime(seg.start - start)}</td>` +
      `<td>${fmtTime(seg.end - start)}</td>` +
      `<td>${seg.duration.toFixed(2)} s</td>` +
      `<td><span class="swatch" style="background:${colorFor(index)}"></span>${seg.label}</td>`;
    body.append(tr);
  });
  const counts = Object.entries(latest.counts)
    .map(([k, v]) => `${k} ${v}`).join(' · ');
  $('cut-summary').textContent =
    `${latest.segments.length} kuvaa · ${fmtTime(latest.program.duration)} · ${counts}`;
  $('counts').textContent = `päätös ${latest.ms} ms`;
}

/* ------------------------------------------------------------ liikenne */

/* Käyttöliittymän koko tila palvelimelle. Sama rakenne kelpaa sekä säätöön että
   vientiin, joten vienti käyttää varmasti sitä mitä ruudulla näkyy. */
function payload() {
  const tracks = {};
  state.media.forEach((m) => { tracks[m.key] = m.config; });
  return { tracks, globals: state.globals };
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
    if (data.ok) {
      banner('');
      drawBar(); renderRuler(); renderLegend(); renderCuts();
    } else {
      banner((data.problems || ['Tuntematon virhe']).join('\n'));
      latest = { ...data, preview: null };
      drawBar(); renderCuts();
    }
  } catch (err) {
    if (err.name !== 'AbortError') banner('Palvelin ei vastaa: ' + err.message, true);
  } finally {
    inflight = null;
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
  button.disabled = true;
  $('status').textContent = 'viedään…';
  try {
    const response = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload()),
    });
    const data = await response.json();
    if (response.ok && data.ok) {
      $('status').textContent = `${data.cuts} kuvaa → ${data.path.split('/').pop()}`;
      banner('');
    } else {
      $('status').textContent = '';
      banner((data.problems || [data.detail || 'Vienti epäonnistui']).join('\n'), true);
    }
  } catch (err) {
    $('status').textContent = '';
    banner('Vienti epäonnistui: ' + err.message, true);
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------ käynnistys */

function renderHeader() {
  $('project-name').textContent = state.name || '—';
  const kind = state.kind === 'project' ? 'projekti' : 'synkkaklippi';
  $('project-meta').textContent =
    `${kind} · ${state.fps ?? '?'} fps · ${state.media.length} mediaa`;
  $('paths').textContent =
    `Vienti: ${state.output_path}\nAsetukset: ${state.settings_path}`;
}

/* Verhokäyrien edistyminen. Roolit saa nimetä laskennan aikana; kun se
   valmistuu, ajetaan päätös kerran automaattisesti. */
function watchProgress() {
  clearInterval(progressTimer);
  if (state.progress && state.progress.ready) { $('status').textContent = ''; return; }
  progressTimer = setInterval(async () => {
    const data = await (await fetch('/api/state')).json();
    state.progress = data.progress;
    state.media.forEach((m) => {
      const fresh = data.media.find((x) => x.key === m.key);
      if (fresh) m.envelope_error = fresh.envelope_error;
    });
    const p = data.progress;
    if (p.ready) {
      clearInterval(progressTimer);
      $('status').textContent = '';
      renderTracks();
      send();
    } else {
      $('status').textContent =
        `verhokäyrät ${p.done}/${p.total}${p.current ? ' · ' + p.current : ''}`;
    }
  }, 400);
}

async function boot() {
  state = await (await fetch('/api/state')).json();
  if (state.error) { banner(state.error, true); return; }
  renderHeader();
  renderTracks();
  renderGlobals();
  watchProgress();
  if (state.progress && state.progress.ready) send();
}

$('export').addEventListener('click', exportXml);
$('reload').addEventListener('click', async () => {
  state = await (await fetch('/api/reload', { method: 'POST' })).json();
  latest = null;
  if (state.error) { banner(state.error, true); return; }
  renderHeader(); renderTracks(); renderGlobals(); watchProgress();
});
window.addEventListener('resize', () => { drawBar(); renderRuler(); });
window.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'e') {
    e.preventDefault();
    exportXml();
  }
});

boot();
