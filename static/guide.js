(async () => {
  const $ = (id) => document.getElementById(id);
  const { esc } = NG;
  const meta = await NG.loadMeta();

  let state = { running: false };
  let mic = null;
  let muted = false;
  let meterRaf = 0;
  let current = null; // latest suggestion
  const history = [];
  let lastEngagement = null;

  // ---------- Setup ----------
  for (const [slug, label] of Object.entries(meta.tour_types)) $("tourType").append(new Option(label, slug));
  NG.fillLanguageSelect($("guideLang"));
  NG.fillLanguageSelect($("guestLangStart"), { includeAuto: true });
  NG.fillLanguageSelect($("guestLang"), { includeAuto: true, autoLabel: "Auto (from guest screens)" });

  const profile = await (await fetch("/api/profile")).json();
  $("guideLang").value = profile.guide_language || "en";

  const ws = NG.connect("guide", onMessage, (open) => {
    if (!open) setPill("conn", "Reconnecting…", "warn");
    else {
      setPill("conn", "Connected", "ok");
      // A reconnect makes a fresh server-side client; keep it muted if we are.
      if (muted) ws.send({ type: "mute", muted: true });
    }
  });

  $("startBtn").onclick = () => start(false);
  $("demoBtn").onclick = () => start(true);

  function start(demo) {
    ws.send({
      type: "start",
      tour_type: $("tourType").value,
      guide_lang: $("guideLang").value,
      guest_lang: $("guestLangStart").value || null,
    });
    if (demo) ws.send({ type: "demo", speed: 1.4 });
  }

  $("stopBtn").onclick = () => {
    stopMic();
    ws.send({ type: "stop" });
  };

  $("micBtn").onclick = async () => {
    if (mic) return stopMic();
    try {
      mic = await NG.startMic((pcm) => {
        if (!muted) ws.sendBinary(pcm);
      });
      $("muteBtn").classList.remove("hidden");
      $("micBtn").textContent = "■ Stop listening";
      $("micBtn").classList.remove("accent");
      $("micBtn").classList.add("on");
      const tick = () => {
        $("meterFill").style.width = `${Math.round(mic.level() * 100)}%`;
        meterRaf = requestAnimationFrame(tick);
      };
      tick();
    } catch (err) {
      NG.toast(`Microphone unavailable: ${err.message}`);
    }
  };

  function setMuted(next) {
    muted = next;
    ws.send({ type: "mute", muted });
    const b = $("muteBtn");
    b.classList.toggle("muted", muted);
    b.setAttribute("aria-pressed", String(muted));
    b.textContent = muted ? "🔇 Muted" : "🔇 Mute";
    $("mutedBanner").classList.toggle("hidden", !muted);
    document.querySelector(".meter").classList.toggle("muted", muted);
    if (muted) $("listening").classList.add("hidden");
  }

  $("muteBtn").onclick = () => setMuted(!muted);

  function stopMic() {
    if (!mic) return;
    if (muted) setMuted(false);
    $("muteBtn").classList.add("hidden");
    mic.stop();
    mic = null;
    cancelAnimationFrame(meterRaf);
    $("meterFill").style.width = "0";
    $("micBtn").textContent = "🎙 Start listening";
    $("micBtn").classList.add("accent");
    $("micBtn").classList.remove("on");
  }


  $("guestLang").onchange = () => ws.send({ type: "pin_language", lang: $("guestLang").value || null });

  $("typeForm").onsubmit = (e) => {
    e.preventDefault();
    const text = $("typeInput").value.trim();
    if (!text) return;
    ws.send({ type: "guide_text", text });
    $("typeInput").value = "";
  };

  $("sayBtn").onclick = () => current && sayToGuest(options()[selected]?.say || current.say);
  $("copyBtn").onclick = async () => {
    if (!current) return;
    try {
      await navigator.clipboard.writeText(options()[selected]?.say || current.say);
      NG.toast("Copied");
    } catch {
      NG.toast("Copy is not available here");
    }
  };

  function sayToGuest(text) {
    ws.send({ type: "say_to_guest", text });
    NG.toast("Sent to the guest screen");
  }

  $("closeReport").onclick = () => $("reportDialog").close();
  $("newTour").onclick = () => {
    $("reportDialog").close();
    $("transcript").innerHTML = "";
    history.length = 0;
    current = null;
    renderSuggestion(null);
  };

  // ---------- Quick phrases ----------
  for (const phrase of profile.quick_phrases || []) {
    const b = document.createElement("button");
    b.textContent = phrase;
    b.title = "Say to the guests in their language";
    b.onclick = () => sayToGuest(phrase);
    $("quickPhrases").append(b);
  }

  // ---------- Keyboard ----------
  document.addEventListener("keydown", (e) => {
    if (!state.running || e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = e.target?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (["1", "2", "3"].includes(e.key) && current) {
      const i = Number(e.key) - 1;
      const o = options()[i];
      if (o) {
        selectOption(i);
        sayToGuest(o.say);
      }
    } else if (e.key === "Enter" && current && tag !== "BUTTON") {
      sayToGuest(options()[selected]?.say || current.say);
    } else if (e.key.toLowerCase() === "m") {
      // M starts listening; once listening it toggles mute.
      if (mic) setMuted(!muted);
      else $("micBtn").click();
    } else if (e.key === "[" || e.key === "]") {
      const stops = state.stops || [];
      const i = stops.findIndex((x) => x.id === state.current_stop);
      const next = stops[Math.max(0, Math.min(stops.length - 1, i + (e.key === "]" ? 1 : -1)))];
      if (next) ws.send({ type: "set_stop", stop: next.id });
    } else {
      return;
    }
    e.preventDefault();
  });

  // ---------- Messages ----------
  function onMessage(msg) {
    switch (msg.type) {
      case "tour_state":
        applyState(msg);
        break;
      case "transcript":
        addTurn(msg.entry);
        break;
      case "partial":
        showPartial(msg);
        break;
      case "partial_cancel":
        partials.get(msg.utt)?.remove();
        partials.delete(msg.utt);
        break;
      case "suggestion":
        renderSuggestion(msg);
        break;
      case "engagement":
        renderEngagement(msg);
        break;
      case "listening":
        $("listening").classList.toggle("hidden", !msg.active);
        if (msg.active) $("listening").textContent = msg.role === "guest" ? "● guest screen mic" : "● hearing speech";
        break;
      case "thinking":
        $("thinking").classList.toggle("hidden", !msg.active);
        break;
      case "tour_ended":
        showReport(msg.report);
        break;
      case "status":
        if (msg.level === "error" || msg.level === "warning") NG.toast(msg.message, 4000);
        else if (msg.message) NG.toast(msg.message);
        break;
    }
  }

  function applyState(s) {
    const wasRunning = state.running;
    state = s;
    $("setup").classList.toggle("hidden", s.running);
    $("live").classList.toggle("hidden", !s.running);
    if (!s.running && wasRunning) stopMic();
    if (s.running) {
      $("guestLang").value = s.guest_lang_pinned ? s.guest_lang || "" : "";
      const auto = $("guestLang").options[0];
      auto.textContent = s.guest_lang && !s.guest_lang_pinned ? `Auto (${NG.langName(s.guest_lang)})` : "Auto (from guest screens)";
      renderStops(lastEngagement);
    }
  }

  function renderStops(engagement) {
    const counts = Object.fromEntries((engagement?.stops || []).map((s) => [s.id, s.questions]));
    const rail = $("stopRail");
    rail.innerHTML = "";
    (state.stops || []).forEach((stop, i) => {
      const b = document.createElement("button");
      b.className = "stop-btn" + (stop.id === state.current_stop ? " current" : "");
      b.innerHTML = `${i + 1}. ${esc(stop.name)}${counts[stop.id] ? ` <span class="badge" title="questions">${counts[stop.id]}</span>` : ""}`;
      b.onclick = () => ws.send({ type: "set_stop", stop: stop.id });
      rail.append(b);
    });
  }

  // Live captions: one bubble per utterance, updated as Whisper re-reads the
  // speech so far, then replaced in place by the translated turn.
  const partials = new Map();
  function showPartial(p) {
    let el = partials.get(p.utt);
    const box = $("transcript");
    const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    if (!el) {
      el = document.createElement("div");
      partials.set(p.utt, el);
      box.append(el);
    }
    el.className = `turn ${p.speaker} partial` + (p.final ? " final-text" : "");
    const who = p.speaker === "guest" ? "Visitor" : "You";
    const lang = p.lang && p.lang !== state.guide_lang ? `<span class="chip sky">${esc(NG.langName(p.lang))}</span>` : "";
    el.innerHTML = `<div class="meta"><span>${who}</span>${lang}<span>${p.final ? "heard" : "speaking…"}</span></div><div class="main" dir="auto">${esc(p.text)}</div>`;
    if (stick) box.scrollTop = box.scrollHeight;
  }

  function addTurn(e) {
    const el = document.createElement("div");
    const guest = e.speaker === "guest";
    el.className = `turn ${e.speaker}` + (e.source === "blink" ? " blink" : "");
    const who = guest ? (e.source === "blink" ? "👁 Visitor (blink)" : e.source === "tap" ? "👆 Visitor (tapped)" : "Visitor") : e.source === "copilot" ? "You · co-pilot line" : "You";
    const lang = e.lang && e.lang !== state.guide_lang ? `<span class="chip sky">${esc(NG.langName(e.lang))}</span>` : "";
    const q = e.tags?.question ? `<span class="q" title="question">?</span>` : "";
    const topic = e.tags?.topic ? `<span class="chip">${esc(e.tags.topic.replace("_", " "))}</span>` : "";
    const untranslated = e.translated === false ? `<span class="untranslated" title="Translation model not loaded">untranslated</span>` : "";
    let main, orig = "";
    if (guest) {
      main = e.text_guide;
      if (e.text_guide !== e.text) orig = `<div class="orig" dir="auto">${esc(e.text)}</div>`;
    } else {
      main = e.text;
      const t = Object.entries(e.translations || {});
      if (t.length) orig = t.map(([code, text]) => `<div class="orig" dir="auto">→ ${esc(code.toUpperCase())}: ${esc(text)}</div>`).join("");
    }
    if (e.deferred) orig += `<div class="orig untranslated">Marked as unanswered: add it to your briefing</div>`;
    const lat = e.latency_ms != null ? `<span class="lat" title="end of speech → translated text">⚡${(e.latency_ms / 1000).toFixed(1)}s</span>` : "";
    el.innerHTML = `<div class="meta">${q}<span>${who}</span>${lang}${topic}${untranslated}${lat}</div><div class="main" dir="auto">${esc(main)}</div>${orig}`;
    const box = $("transcript");
    const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    const live = e.utt && partials.get(e.utt);
    if (live) {
      live.replaceWith(el);
      partials.delete(e.utt);
      el.classList.add("landed");
    } else {
      box.append(el);
    }
    if (stick) box.scrollTop = box.scrollHeight;
  }

  function renderSuggestion(s) {
    if (current) {
      history.unshift(current);
      if (history.length > 8) history.pop();
    }
    current = s;
    const text = $("sugText");
    if (!s) {
      text.textContent = "A suggestion appears after every line, yours or a visitor's.";
      $("sugSituation").classList.add("hidden");
      $("sugAfter").textContent = "";
      text.className = "sug-text muted";
      $("sugWhy").textContent = "";
      $("sugGuest").classList.add("hidden");
      $("sayBtn").disabled = $("copyBtn").disabled = true;
      $("sugSource").textContent = "";
      $("sugStep").classList.add("hidden");
      $("sugOptions").innerHTML = "";
      renderHistory();
      return;
    }
    selected = 0;
    renderOptions();
    text.textContent = s.say;
    text.className = "sug-text";
    void text.offsetWidth;
    text.classList.add("fresh");
    $("sugWhy").textContent = s.why || "";
    $("sugSituation").textContent = s.situation || "";
    $("sugSituation").classList.toggle("hidden", !s.situation);
    $("sugAfter").textContent = s.after === "guide" ? "your next move" : "reply to the visitor";
    $("sugSource").textContent = s.source === "llm" ? "on-device model" : "farm FAQ";
    if (s.guest_lang && s.say_guest && s.say_guest !== s.say) {
      $("sugGuest").innerHTML = `<span class="chip sky">${esc(NG.langName(s.guest_lang))}</span> <span dir="auto">${esc(s.say_guest)}</span>`;
      $("sugGuest").classList.remove("hidden");
    } else {
      $("sugGuest").classList.add("hidden");
    }
    const steps = { buy: "→ sale", book: "→ booking", return: "→ return visit", referral: "→ referral", review: "→ review" };
    $("sugStep").textContent = steps[s.next_step] || "";
    $("sugStep").classList.toggle("hidden", !steps[s.next_step]);
    $("sayBtn").disabled = $("copyBtn").disabled = false;
    renderHistory();
  }

  let selected = 0;
  function options() {
    return current?.options?.length ? current.options : current ? [{ label: "Say", say: current.say }] : [];
  }
  function renderOptions() {
    const box = $("sugOptions");
    box.innerHTML = "";
    const opts = options();
    if (opts.length < 2) return;
    opts.forEach((o, i) => {
      const b = document.createElement("button");
      b.className = i === selected ? "on" : "";
      b.innerHTML = `<kbd>${i + 1}</kbd> ${esc(o.label)}`;
      b.title = o.say;
      b.onclick = () => selectOption(i);
      box.append(b);
    });
  }
  function selectOption(i) {
    const o = options()[i];
    if (!o) return;
    selected = i;
    $("sugText").textContent = o.say;
    renderOptions();
    if (i > 0) $("sugGuest").classList.add("hidden");
  }

  function renderHistory() {
    const ol = $("sugHistory");
    ol.innerHTML = "";
    for (const h of history) {
      const li = document.createElement("li");
      li.textContent = h.say;
      const b = document.createElement("button");
      b.textContent = "Say";
      b.onclick = () => sayToGuest(h.say);
      li.append(b);
      ol.append(li);
    }
  }

  function renderEngagement(e) {
    lastEngagement = e;
    $("qCount").textContent = `${e.question_count} question${e.question_count === 1 ? "" : "s"}`;
    NG.bars($("stopBars"), e.stops, {
      hotId: e.hot_stop,
      currentId: e.current_stop,
      onClick: (row) => ws.send({ type: "set_stop", stop: row.id }),
    });
    $("hotTopic").textContent = `Hot topic: ${e.hot_topic_label || "–"}`;
    $("mood").textContent = `Mood: ${e.overall_mood}`;
    $("blinkCount").textContent = `👁 ${e.blink_turns} blink`;
    $("used").textContent = `${e.suggestions_used || 0}/${e.suggestion_count || 0} suggestions used`;
    $("intents").innerHTML = Object.entries(e.intents || {})
      .map(([k, v]) => `<span class="chip">${esc(k.replace("_", " "))} ×${v}</span>`)
      .join("");
    const deferred = e.deferred_questions || [];
    $("deferredWrap").classList.toggle("hidden", !deferred.length);
    $("deferred").innerHTML = deferred.map((d) => `<li>${esc(d)}</li>`).join("");
    renderStops(e);
  }

  function showReport(r) {
    if (!r) return;
    const stat = (n, label) => `<div class="stat"><b>${esc(n ?? "–")}</b><span class="small muted">${esc(label)}</span></div>`;
    const list = (items) => (items?.length ? `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : `<p class="muted small">None.</p>`);
    $("reportBody").innerHTML = `
      <div class="report-grid">
        ${stat(r.question_count, "visitor questions")}
        ${stat(r.hot_stop || "–", "most engaging stop")}
        ${stat(`${r.suggestions_used}/${r.suggestions}`, "suggestions used")}
        ${stat((r.guest_languages || []).map(NG.langName).join(", ") || "–", "guest languages")}
        ${stat(r.blink_turns, "blink messages")}
      </div>
      <h2>Questions by stop</h2><div id="reportBars" class="bars"></div>
      <h2 style="margin-top:14px">Recommendations</h2>${list(r.recommendations)}
      ${r.missed_moments?.length ? `<h2>Missed moments</h2>${list(r.missed_moments)}` : ""}
      ${r.unanswered?.length ? `<h2>Add these answers to your briefing</h2>${list(r.unanswered)}` : ""}`;
    NG.bars($("reportBars"), r.stops || []);
    $("reportDialog").showModal();
  }

  // ---------- Engine status ----------
  const pills = {};
  function setPill(key, label, level) {
    if (!pills[key]) {
      pills[key] = document.createElement("span");
      $("enginePills").append(pills[key]);
    }
    pills[key].className = `pill ${level}`;
    pills[key].innerHTML = `<span class="dot"></span>${esc(label)}`;
  }

  async function pollHealth() {
    try {
      const h = await (await fetch("/api/health")).json();
      setPill("stt", h.stt.ready ? `Whisper ${h.stt.model}` : h.stt.error ? "Speech model error" : "Loading speech…", h.stt.ready ? "ok" : h.stt.error ? "err" : "warn");
      setPill("mt", h.translation.ready ? "NLLB translation" : h.translation.error ? "Translation offline" : "Loading translation…", h.translation.ready ? "ok" : h.translation.error ? "err" : "warn");
      setPill("llm", h.llm.ready ? h.llm.name.replace("ollama:", "") : "FAQ rules (no model)", h.llm.ready ? "ok" : "warn");
      if (!(h.stt.ready && h.translation.ready)) setTimeout(pollHealth, 4000);
      else setTimeout(pollHealth, 30000);
    } catch {
      setTimeout(pollHealth, 5000);
    }
  }
  pollHealth();
})();
