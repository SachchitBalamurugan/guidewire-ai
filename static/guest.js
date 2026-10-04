(async () => {
  const $ = (id) => document.getElementById(id);
  const { esc } = NG;
  const B = window.NoorBlink;
  await NG.loadMeta();

  let state = { running: false };
  let soundOn = false;
  let myLang = "";
  let mic = null;
  const earlier = [];

  NG.fillLanguageSelect($("myLang"), { includeAuto: true, autoLabel: "Automatic" });
  try {
    myLang = localStorage.getItem("noor-guide.guest-lang") || "";
  } catch {
    /* private mode */
  }
  $("myLang").value = myLang;

  const profile = await (await fetch("/api/profile")).json();
  $("farmName").textContent = profile.business_name || "Welcome";

  // ---------- Welcome: pick a language, and the Start tap unlocks speech on mobile ----------
  NG.fillLanguageSelect($("welcomeLang"), { includeAuto: true, autoLabel: "Same as the tour (automatic)" });
  $("welcomeLang").value = myLang;
  let welcomed = false;
  try {
    welcomed = sessionStorage.getItem("noor-guide.welcomed") === "1";
  } catch {
    /* private mode */
  }
  $("welcomeTitle").textContent = profile.business_name ? `Welcome to ${profile.business_name}` : "Welcome";
  $("welcome").classList.toggle("hidden", welcomed);
  function closeWelcome(withSound) {
    $("myLang").value = $("welcomeLang").value;
    $("myLang").onchange();
    if (withSound && !soundOn) $("soundBtn").onclick();
    $("welcome").classList.add("hidden");
    try {
      sessionStorage.setItem("noor-guide.welcomed", "1");
    } catch {
      /* private mode */
    }
  }
  $("welcomeStart").onclick = () => closeWelcome(true);
  $("welcomeQuiet").onclick = () => closeWelcome(false);

  const ws = NG.connect("guest", onMessage, (open) => {
    if (open && myLang) ws.send({ type: "guest_language", lang: myLang });
  });

  // ---------- Tap-to-ask ----------
  async function renderTapGrid() {
    const sentences = Object.values(B.SHORTCUTS).filter((t) => t.length > 6);
    let labels = sentences;
    const lang = myLang || state.guest_lang;
    if (lang && lang !== "en") {
      try {
        const res = await fetch("/api/translate_batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ texts: sentences, src: "en", tgt: lang }),
        });
        labels = (await res.json()).texts;
      } catch {
        /* show English */
      }
      // The language changed while we waited: a newer render owns the grid.
      if (lang !== (myLang || state.guest_lang)) return;
    }
    const grid = $("tapGrid");
    grid.innerHTML = "";
    sentences.forEach((en, i) => {
      const b = document.createElement("button");
      b.textContent = labels[i];
      b.dir = "auto";
      b.onclick = () => {
        if (!state.running) return NG.toast("The tour has not started yet.");
        ws.send({ type: "guest_text", text: en, source: "tap", lang: "en" });
        b.classList.add("sent");
        setTimeout(() => b.classList.remove("sent"), 1500);
        addSent(`“${labels[i]}”`, "hand");
      };
      grid.append(b);
    });
  }
  let tapLang = null;
  function refreshTapGrid() {
    const lang = myLang || state.guest_lang || "en";
    if (lang !== tapLang) {
      tapLang = lang;
      renderTapGrid();
    }
  }
  refreshTapGrid();

  $("myLang").onchange = () => {
    myLang = $("myLang").value;
    try {
      localStorage.setItem("noor-guide.guest-lang", myLang);
    } catch {
      /* private mode */
    }
    ws.send({ type: "guest_language", lang: myLang || null });
    refreshTapGrid();
  };

  $("soundBtn").onclick = () => {
    soundOn = !soundOn;
    $("soundBtn").innerHTML = `${NG.icon(soundOn ? "volume" : "mute")}<span>${soundOn ? "Sound on" : "Turn on sound"}</span>`;
    $("soundBtn").classList.toggle("on", soundOn);
    $("soundBtn").setAttribute("aria-pressed", String(soundOn));
    // A user gesture unlocks speech synthesis on mobile browsers.
    if (soundOn) window.speechSynthesis?.speak(new SpeechSynthesisUtterance(""));
    else window.speechSynthesis?.cancel();
  };

  // ---------- Tabs ----------
  let blink = null;
  document.querySelectorAll(".tabs button").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b === btn));
      for (const t of ["speak", "type", "blink"]) $(`tab-${t}`).classList.toggle("hidden", t !== btn.dataset.tab);
      if (btn.dataset.tab === "blink") ensureBlink();
    };
  });

  // ---------- Speak ----------
  $("guestMic").onclick = async () => {
    if (mic) {
      mic.stop();
      mic = null;
      setGuestMic(false);
      return;
    }
    if (!state.running) return NG.toast("The tour has not started yet.");
    try {
      // While this screen is reading a translation aloud, its own mic would
      // hear it and send it back as a visitor question. Hold the mic until
      // the speech (plus a short tail) is over.
      let quietUntil = 0;
      mic = await NG.startMic((pcm) => {
        const now = performance.now();
        if (window.speechSynthesis?.speaking) quietUntil = now + 600;
        if (now >= quietUntil) ws.sendBinary(pcm);
      });
      setGuestMic(true);
    } catch (err) {
      NG.toast(`Microphone unavailable: ${err.message}`);
    }
  };

  function setGuestMic(on) {
    $("guestMic").classList.toggle("on", on);
    $("guestMic").setAttribute("aria-pressed", String(on));
    $("guestMic").setAttribute("aria-label", on ? "Stop" : "Start speaking");
    $("guestMic").querySelector(".big-mic-ico").innerHTML = NG.icon(on ? "stop" : "mic");
    $("guestMicLabel").textContent = on ? "Listening… tap when you're done" : "Tap the button, then ask";
  }

  // ---------- Type ----------
  $("typeForm").onsubmit = (e) => {
    e.preventDefault();
    const text = $("typeText").value.trim();
    if (!text) return;
    sendText(text, "typed", myLang || state.guest_lang || null);
    $("typeText").value = "";
  };

  function sendText(text, source, lang) {
    if (!state.running) return NG.toast("The tour has not started yet.");
    ws.send({ type: "guest_text", text, source, lang });
  }

  // ---------- Blink ----------
  const shortcutGrid = $("shortcutGrid");
  for (const [word, sentence] of Object.entries(B.SHORTCUTS)) {
    const code = [...word].map((ch) => B.prettyCode(B.MORSE[ch] || "")).join(" ");
    const div = document.createElement("div");
    div.className = "shortcut";
    div.innerHTML = `<b>${esc(word)}</b><span class="code">${esc(code)}</span><br>${esc(sentence)}`;
    shortcutGrid.append(div);
  }

  let typed = "";

  function ensureBlink() {
    if (blink) return;
    blink = new B.BlinkSession($("cam"));
    blink.onEvents = (events) => {
      for (const e of events) {
        typed = B.applyEvent(typed, e);
        if (e.type === "send") sendBlink();
        if (e.type === "letter" && soundOn) NG.speak(e.char, "en-US", { rate: 1.2 });
      }
      renderBlink();
    };
    blink.onFrame = (f) => {
      const el = $("eyeState");
      el.textContent = !f.faceFound ? "no face" : f.closed ? "eyes closed" : `eyes open · ${f.fps} fps`;
      el.classList.toggle("closed", f.closed);
    };
    blink.onTick = (now) => {
      const p = blink.decoder.phase(now);
      const ring = $("ring");
      if (p.kind === "closed") {
        const t = blink.decoder.timing;
        ring.style.setProperty("--p", Math.min(1, p.ms / t.deleteMs));
        ring.style.setProperty("--c", p.as === "delete" ? "var(--danger)" : p.as === "dash" ? "var(--terra)" : "var(--olive)");
        $("ringLabel").innerHTML = p.as === "ignored" ? "…" : p.as === "dot" ? "·" : p.as === "dash" ? "−" : NG.icon("backspace");
      } else if (p.kind === "letter") {
        ring.style.setProperty("--p", p.progress);
        ring.style.setProperty("--c", "var(--sky)");
        $("ringLabel").textContent = B.previewFor(blink.decoder.buffer) || "";
      } else {
        ring.style.setProperty("--p", 0);
        $("ringLabel").textContent = "·";
      }
      renderPending();
    };
  }

  function renderPending() {
    const code = blink?.decoder.buffer || "";
    $("pendingCode").textContent = B.prettyCode(code);
    $("pendingPreview").innerHTML = code ? `${NG.icon("arrowRight")}${esc(B.previewFor(code))}` : "";
  }

  function renderBlink() {
    $("typed").textContent = typed;
    renderPending();
  }

  function sendBlink() {
    const raw = typed.trim();
    typed = "";
    renderBlink();
    if (!raw) return;
    const { text, expanded } = B.expandShortcut(raw);
    sendText(text, "blink", "en");
    addSent(expanded ? `${raw}: “${text}”` : `“${text}”`, "eye");
  }

  $("camBtn").onclick = async () => {
    ensureBlink();
    if (blink.cameraOn) {
      blink.stopCamera();
      $("camBtn").querySelector("span").textContent = "Start camera";
      $("eyeState").textContent = "camera off";
      return;
    }
    $("camBtn").disabled = true;
    try {
      await blink.startCamera();
      $("camBtn").querySelector("span").textContent = "Stop camera";
    } catch (err) {
      NG.toast(`Camera unavailable: ${err.message}. Hold Space instead.`, 4000);
    } finally {
      $("camBtn").disabled = false;
    }
  };

  $("calBtn").onclick = async () => {
    ensureBlink();
    if (!blink.cameraOn) return NG.toast("Start the camera first.");
    try {
      await B.quickCalibrate(blink, (step, message) => {
        NG.toast(message, 2000);
        if (soundOn && step === "closed") NG.speak("Close your eyes", "en-US");
        if (soundOn && step === "done") NG.speak("Done", "en-US");
      });
    } catch (err) {
      NG.toast(err.message, 5000);
    }
  };

  $("blinkDemoBtn").onclick = () => {
    ensureBlink();
    if (blink.demoPlaying) {
      blink.stopDemo();
      return;
    }
    blink.playDemo(B.demoTimeline("BUY", blink.decoder.timing), (note) => NG.toast(note, 2500), () => NG.toast("Demo finished"));
  };

  $("blinkSend").onclick = () => sendBlink();
  $("blinkClear").onclick = () => {
    typed = "";
    blink?.decoder.reset();
    renderBlink();
  };

  // ---------- Messages ----------
  function onMessage(msg) {
    if (msg.type === "tour_state") {
      // Re-announce the language when a tour starts, in case the server
      // restarted since this screen chose it.
      if (msg.running && !state.running && myLang) ws.send({ type: "guest_language", lang: myLang });
      state = msg;
      $("tourStatus").textContent = msg.running ? "Tour in progress" : "Waiting for the tour to start…";
      document.body.classList.toggle("tour-live", !!msg.running);
      $("guideMuted").classList.toggle("hidden", !(msg.running && msg.guide_muted));
      const auto = $("myLang").options[0];
      auto.textContent = msg.guest_lang ? `Automatic (${NG.langName(msg.guest_lang)})` : "Automatic";
      refreshTapGrid();
    } else if (msg.type === "guest_line") {
      $("liveCap").classList.add("hidden");
      showLine(msg);
    } else if (msg.type === "partial") {
      // The guide (or a visitor) is mid-sentence: show the words as heard, so
      // the screen reacts before the translation lands.
      $("liveCap").textContent = msg.text;
      $("liveCap").classList.remove("hidden");
    } else if (msg.type === "mic_state" && msg.role === "guide") {
      $("guideMuted").classList.toggle("hidden", !msg.muted);
      if (msg.muted) $("liveCap").classList.add("hidden");
    } else if (msg.type === "partial_cancel") {
      $("liveCap").classList.add("hidden");
    } else if (msg.type === "transcript" && msg.entry.utt) {
      if (msg.entry.speaker === "guest" || !Object.keys(msg.entry.translations || {}).length) $("liveCap").classList.add("hidden");
    }
    if (msg.type === "transcript" && msg.entry.speaker === "guest" && msg.entry.source !== "blink" && msg.entry.source !== "tap") {
      addSent(`“${msg.entry.text}”`, msg.entry.source === "typed" ? "keyboard" : "mic");
    } else if (msg.type === "tour_ended") {
      showText({ text: "Thank you for visiting. We hope to see you again.", bcp47: "en-US", rtl: false }, "");
    }
  }

  function pickLine(msg) {
    const lines = msg.lines || {};
    const want = myLang || msg.lang;
    if (want && lines[want]) return lines[want];
    if (want && want === msg.original_lang) return { text: msg.original, bcp47: null, rtl: false };
    const first = Object.values(lines)[0];
    return first || { text: msg.original, bcp47: null, rtl: false };
  }

  function showLine(msg) {
    const line = pickLine(msg);
    showText(line, line.text !== msg.original ? msg.original : "");
    if (soundOn && msg.speak && line.bcp47) {
      window.speechSynthesis?.cancel();
      if (!NG.speak(line.text, line.bcp47)) NG.toast("No voice for this language on this device: showing text only.");
    }
  }

  function showText(line, original) {
    const prev = $("nowText").textContent;
    if (prev && prev !== "…") {
      earlier.unshift(prev);
      if (earlier.length > 6) earlier.pop();
      $("earlier").innerHTML = earlier.map((t) => `<li dir="auto">${esc(t)}</li>`).join("");
    }
    const el = $("nowText");
    el.textContent = line.text;
    el.dir = line.rtl ? "rtl" : "auto";
    el.classList.remove("fresh");
    void el.offsetWidth;
    el.classList.add("fresh");
    $("nowOrig").textContent = original;
  }

  function addSent(text, iconName = "check") {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `${NG.icon(iconName)}<span dir="auto">${esc(text)}</span>`;
    $("sentList").prepend(div);
    while ($("sentList").children.length > 4) $("sentList").lastChild.remove();
  }
})();
