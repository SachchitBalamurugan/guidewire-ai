// Shared by the guide console and the guest screen.

// Where the AI runs. When the laptop serves these pages, it's this same address.
// When the pages are hosted elsewhere (Vercel; see vercel.json), they talk to the
// laptop's HTTPS address, given once as ?api=https://… and remembered.
const GW = (() => {
  const KEY = "guidewire.api";
  const remote = window.GW_STATIC_HOST === true;
  let api = "";
  if (remote) {
    const fromLink = new URLSearchParams(location.search).get("api");
    try {
      if (fromLink) localStorage.setItem(KEY, fromLink);
      api = fromLink || localStorage.getItem(KEY) || "";
    } catch {
      api = fromLink || "";
    }
    api = api.trim().replace(/\/+$/, "");
    if (api && !/^https?:\/\//i.test(api)) api = `https://${api}`;
  }

  if (api) {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, init) =>
      typeof input === "string" && input.startsWith("/api/") ? nativeFetch(api + input, init) : nativeFetch(input, init);
  }

  const wsUrl = (path) =>
    api ? api.replace(/^http/i, "ws") + path : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;

  // Links to the guest screen carry the laptop address, so a guest's phone finds it too.
  const withApi = (href) => (api ? `${href}${href.includes("?") ? "&" : "?"}api=${encodeURIComponent(api)}` : href);

  function connectBox(problem, showApi = false) {
    const box = document.createElement("div");
    box.className = "connect";
    box.innerHTML = `
      <form class="connect-card">
        <h2>Connect to the Guidewire laptop</h2>
        <p>${problem}</p>
        <label>Laptop link<input name="api" type="url" required placeholder="https://….trycloudflare.com"></label>
        <button class="btn primary lg" type="submit">Connect</button>
        <small>The AI runs on a laptop. Start it with <code>share.ps1</code> or <code>share.sh</code>, which prints this link.</small>
      </form>`;
    box.querySelector("input").value = api;
    if (showApi) box.querySelector("p b").textContent = api;
    box.querySelector("form").onsubmit = (e) => {
      e.preventDefault();
      const url = new URL(location.href);
      url.searchParams.set("api", e.target.api.value.trim());
      location.href = url.toString();
    };
    document.body.append(box);
  }

  if (remote) {
    document.addEventListener("DOMContentLoaded", async () => {
      document.querySelectorAll('a[href^="/guest"]').forEach((a) => a.setAttribute("href", withApi(a.getAttribute("href"))));
      if (!api) return connectBox("These pages are hosted online. Paste the link of the laptop that runs the AI.");
      try {
        const ctl = new AbortController();
        setTimeout(() => ctl.abort(), 8000);
        const res = await fetch("/api/health", { signal: ctl.signal });
        if (!res.ok) throw new Error(res.status);
      } catch {
        connectBox("Can't reach the laptop at <b></b>. Check it's on and running <code>share</code>, or paste its new link.", true);
      }
    });
  }

  return { api, remote, wsUrl, withApi };
})();

const NG = (() => {
  let languages = [];
  let langByCode = {};

  async function loadMeta() {
    const res = await fetch("/api/meta");
    const meta = await res.json();
    languages = meta.languages;
    langByCode = Object.fromEntries(languages.map((l) => [l.code, l]));
    return meta;
  }

  function langName(code) {
    return langByCode[code]?.name || code || "";
  }

  function langNative(code) {
    return langByCode[code]?.native || langName(code);
  }

  function fillLanguageSelect(select, { includeAuto = false, autoLabel = "Detect automatically" } = {}) {
    select.innerHTML = "";
    if (includeAuto) select.append(new Option(autoLabel, ""));
    for (const l of languages) {
      select.append(new Option(l.native === l.name ? l.name : `${l.name} · ${l.native}`, l.code));
    }
  }

  /** Reconnecting WebSocket. `onMessage` gets parsed JSON. */
  function connect(role, onMessage, onState) {
    let ws = null;
    let closedByUs = false;
    let retry = 0;
    const api = {
      send(obj) {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
      },
      sendBinary(buf) {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(buf);
      },
      get open() {
        return !!ws && ws.readyState === WebSocket.OPEN;
      },
      close() {
        closedByUs = true;
        ws?.close();
      },
    };
    const open = () => {
      ws = new WebSocket(GW.wsUrl(`/ws?role=${role}`));
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        retry = 0;
        onState?.(true);
      };
      ws.onmessage = (event) => {
        try {
          onMessage(JSON.parse(event.data));
        } catch (err) {
          console.error(err);
        }
      };
      ws.onclose = () => {
        onState?.(false);
        if (closedByUs) return;
        retry = Math.min(retry + 1, 6);
        setTimeout(open, 400 * retry);
      };
    };
    open();
    return api;
  }

  /** Microphone → 16 kHz PCM chunks (orbit.ai's worklet). Returns a stop function. */
  async function startMic(onChunk) {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const ctx = new AudioContext();
    await ctx.audioWorklet.addModule("/static/audio-worklet.js");
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "pcm-downsampler");
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    node.port.onmessage = (event) => {
      if (event.data.type === "audio") onChunk(event.data.pcm);
    };
    source.connect(node);
    // The worklet must be pulled by the graph to run; a muted gain keeps the
    // tour audio from playing back through the speakers.
    const mute = ctx.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(ctx.destination);
    const level = () => {
      const data = new Uint8Array(analyser.fftSize);
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) sum += (v - 128) * (v - 128);
      return Math.min(1, Math.sqrt(sum / data.length) / 40);
    };
    return {
      level,
      stop() {
        stream.getTracks().forEach((t) => t.stop());
        node.disconnect();
        ctx.close();
      },
    };
  }

  // ---------- Speech synthesis ----------
  let voices = [];
  const loadVoices = () => (voices = window.speechSynthesis?.getVoices() || []);
  if (window.speechSynthesis) {
    loadVoices();
    window.speechSynthesis.onvoiceschanged = loadVoices;
  }

  function voiceFor(bcp47) {
    if (!bcp47) return null;
    const base = bcp47.split("-")[0].toLowerCase();
    return (
      voices.find((v) => v.lang.toLowerCase() === bcp47.toLowerCase()) ||
      voices.find((v) => v.lang.toLowerCase().startsWith(base)) ||
      null
    );
  }

  /** Speaks in the given language. Returns false when the device has no voice for it. */
  function speak(text, bcp47, { rate = 0.95 } = {}) {
    if (!window.speechSynthesis || !text) return false;
    const voice = voiceFor(bcp47);
    if (!voice) return false;
    const u = new SpeechSynthesisUtterance(text);
    u.voice = voice;
    u.lang = voice.lang;
    u.rate = rate;
    window.speechSynthesis.speak(u);
    return true;
  }


  // ---------- Icons (24px stroke set) ----------
  const ICONS = {
    leaf: '<path d="M5 20c0-9 6-15 15-16-.5 9.5-6.5 16-15 16z" fill="currentColor" stroke="none"/><path d="M5 20 13.5 11.5" stroke="rgba(0,0,0,.28)"/>',
    mic: '<rect x="9" y="2.5" width="6" height="12" rx="3"/><path d="M18.5 10.5v.5a6.5 6.5 0 0 1-13 0v-.5"/><path d="M12 17.5V21"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2.5" fill="currentColor" stroke="none"/>',
    volume: '<path d="M11 5 6.5 9H3v6h3.5L11 19z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
    mute: '<path d="M11 5 6.5 9H3v6h3.5L11 19z"/><path d="m22 9-6 6"/><path d="m16 9 6 6"/>',
    globe: '<circle cx="12" cy="12" r="9.5"/><path d="M2.5 12h19"/><path d="M12 2.5a14.5 14.5 0 0 1 0 19 14.5 14.5 0 0 1 0-19z"/>',
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    keyboard: '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M6.5 9.5h.01M10 9.5h.01M14 9.5h.01M17.5 9.5h.01M7 15h10"/>',
    send: '<path d="M12 19V5"/><path d="m5.5 11.5 6.5-6.5 6.5 6.5"/>',
    copy: '<rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M5 15H4.5A1.5 1.5 0 0 1 3 13.5v-9A1.5 1.5 0 0 1 4.5 3h9A1.5 1.5 0 0 1 15 4.5V5"/>',
    external: '<path d="M7 17 17 7"/><path d="M8 7h9v9"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    play: '<path d="M8 5.5v13l11-6.5z" fill="currentColor" stroke="none"/>',
    sparkles: '<path d="M12 3.5l1.7 4.8 4.8 1.7-4.8 1.7L12 16.5l-1.7-4.8L5.5 10l4.8-1.7z"/><path d="M19 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
    check: '<path d="M20 6.5 9 17.5l-5-5"/>',
    arrowRight: '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    tablet: '<rect x="4.5" y="2.5" width="15" height="19" rx="3"/><path d="M11 18.5h2"/>',
    shield: '<path d="M12 21.5s8-3.8 8-10V5.2L12 2.5 4 5.2v6.3c0 6.2 8 10 8 10z"/><path d="m8.8 12 2.2 2.2 4.2-4.4"/>',
    waves: '<path d="M3 10v4M7 7v10M11 4v16M15 8v8M19 6v12"/>',
    chat: '<path d="M20.5 14.5a2 2 0 0 1-2 2H8l-4.5 4V5.5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z"/>',
    upload: '<path d="M20.5 15v3.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2V15"/><path d="m16.5 8-4.5-4.5L7.5 8"/><path d="M12 3.5v12"/>',
    trash: '<path d="M3.5 6h17"/><path d="M18.5 6v13.5a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2V6"/><path d="M8.5 6V4.5a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2V6"/>',
    camera: '<path d="M22 18.5a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-10a2 2 0 0 1 2-2h3.5l2-3h5l2 3H20a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="3.8"/>',
    target: '<circle cx="12" cy="12" r="9.5"/><circle cx="12" cy="12" r="5.5"/><circle cx="12" cy="12" r="1.5"/>',
    backspace: '<path d="M21 4.5H8.5L2 12l6.5 7.5H21a1.5 1.5 0 0 0 1.5-1.5V6A1.5 1.5 0 0 0 21 4.5z"/><path d="m17.5 9.5-5 5"/><path d="m12.5 9.5 5 5"/>',
    pin: '<path d="M19.5 10c0 5.5-7.5 11.5-7.5 11.5S4.5 15.5 4.5 10a7.5 7.5 0 0 1 15 0z"/><circle cx="12" cy="10" r="2.8"/>',
    flame: '<path d="M12 21.5a6.5 6.5 0 0 0 6.5-6.5c0-4-3-6-4.5-10-2 1.5-3 3.5-3 5.5-1-.5-2-1.5-2.3-3C7 9.2 5.5 11.5 5.5 15a6.5 6.5 0 0 0 6.5 6.5z"/>',
    smile: '<circle cx="12" cy="12" r="9.5"/><path d="M8 14.5s1.5 2 4 2 4-2 4-2"/><path d="M9 9.5h.01M15 9.5h.01"/>',
    thumb: '<circle cx="12" cy="12" r="9.5"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.4-3.4a6 6 0 0 1-7.9 7.9l-6.6 6.6a2.1 2.1 0 0 1-3-3l6.6-6.6a6 6 0 0 1 7.9-7.9z"/>',
    bulb: '<path d="M9 18h6M10 21.5h4"/><path d="M15 14.5c.2-1 .7-1.7 1.4-2.5A5.5 5.5 0 1 0 7.5 12c.7.8 1.2 1.5 1.4 2.5"/>',
    doc: '<path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"/><path d="M14 2.5V8h5.5"/><path d="M8.5 13h7M8.5 17h7"/>',
    route: '<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8.5 18H16a3.5 3.5 0 0 0 0-7H8a3.5 3.5 0 0 1 0-7h7.5"/>',
    users: '<path d="M16 20.5v-1.5a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1.5"/><circle cx="9" cy="7.5" r="3.8"/><path d="M22 20.5v-1.5a4 4 0 0 0-3-3.9M16 3.6a3.8 3.8 0 0 1 0 7.6"/>',
    star: '<path d="m12 2.8 2.9 5.8 6.4.9-4.6 4.5 1.1 6.4L12 17.4l-5.8 3 1.1-6.4-4.6-4.5 6.4-.9z"/>',
    logo: '<g transform="translate(-2 .7)" stroke-width="2.2"><path d="M16.5 8.2A6.4 6.4 0 1 0 18.6 12.9H13.6" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"/><path d="M16.1 8.9C16.3 5.6 18.6 3.6 22.2 3.3C21.9 6.7 19.6 8.9 16.1 8.9Z" fill="currentColor"/><path d="M16.7 8.3 19.5 5.8" stroke="#2f4419" stroke-opacity=".3" stroke-width=".6" stroke-linecap="round"/></g>',
    micOff: '<path d="M2 2l20 20"/><path d="M9 9v2a3 3 0 0 0 5.1 2.1M15 9.3V5.5a3 3 0 0 0-5.7-1.3"/><path d="M18.5 10.5v.5a6.5 6.5 0 0 1-.9 3.3M5.5 10.5v.5a6.5 6.5 0 0 0 10.4 5.2"/><path d="M12 17.5V21"/>',
    starFill: '<path d="m12 2.8 2.9 5.8 6.4.9-4.6 4.5 1.1 6.4L12 17.4l-5.8 3 1.1-6.4-4.6-4.5 6.4-.9z" fill="currentColor"/>',
    languages: '<path d="m5 8 6 6"/><path d="m4 14 6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="m22 22-5-10-5 10"/><path d="M14 18h6"/>',
    plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
    sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    help: '<circle cx="12" cy="12" r="9.5"/><path d="M9.3 9a2.8 2.8 0 0 1 5.4 1c0 1.9-2.7 2.5-2.7 4"/><path d="M12 17.5h.01"/>',
    pause: '<rect x="7" y="5.5" width="3.5" height="13" rx="1" fill="currentColor" stroke="none"/><rect x="13.5" y="5.5" width="3.5" height="13" rx="1" fill="currentColor" stroke="none"/>',
    hand: '<path d="M18 11V6.5a1.5 1.5 0 0 0-3 0V11M15 10.5v-6a1.5 1.5 0 0 0-3 0v6M12 10.5V5.5a1.5 1.5 0 0 0-3 0V14"/><path d="M18 8.5a1.5 1.5 0 0 1 3 0V14a7.5 7.5 0 0 1-7.5 7.5h-1.6a7.5 7.5 0 0 1-5.3-2.2L3.4 16a1.6 1.6 0 0 1 2.3-2.3L9 15"/>',
  };

  function icon(name, cls = "") {
    const body = ICONS[name];
    if (!body) return "";
    return `<svg class="i ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  }

  /** Replaces every <i data-icon="name"></i> in the page (or `root`) with its SVG. */
  function hydrateIcons(root = document) {
    root.querySelectorAll("i[data-icon]").forEach((el) => {
      el.outerHTML = icon(el.dataset.icon, el.className);
    });
  }
  hydrateIcons();

  function toast(message, ms = 2600) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = message;
    document.body.append(el);
    setTimeout(() => el.remove(), ms);
  }

  function esc(text) {
    return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function bars(container, rows, { valueKey = "questions", labelKey = "name", hotId = null, currentId = null, onClick = null } = {}) {
    const max = Math.max(1, ...rows.map((r) => r[valueKey] || 0));
    container.innerHTML = "";
    for (const r of rows) {
      const row = document.createElement("div");
      row.className = "bar-row" + (hotId && r.id === hotId ? " hot" : "") + (currentId && r.id === currentId ? " current" : "");
      row.innerHTML = `<span class="bar-label">${esc(r[labelKey])}</span><span class="bar-track"><span class="bar-fill" style="width:${((r[valueKey] || 0) / max) * 100}%"></span></span><span class="bar-num">${r[valueKey] || 0}</span>`;
      if (onClick) {
        row.style.cursor = "pointer";
        row.addEventListener("click", () => onClick(r));
      }
      container.append(row);
    }
    if (!rows.length) container.innerHTML = `<p class="muted small">Nothing yet.</p>`;
  }

  return { loadMeta, langName, langNative, fillLanguageSelect, connect, startMic, speak, voiceFor, toast, esc, bars, icon, hydrateIcons };
})();
