// Shared by the guide console and the guest screen.

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
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?role=${role}`);
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

  return { loadMeta, langName, langNative, fillLanguageSelect, connect, startMic, speak, voiceFor, toast, esc, bars };
})();
