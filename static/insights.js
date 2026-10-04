(async () => {
  const $ = (id) => document.getElementById(id);
  const { esc } = NG;
  await NG.loadMeta();
  let reviews = [];
  const reviewById = () => Object.fromEntries(reviews.map((r) => [r.id, r]));

  // ---------- Reviews ----------
  async function loadReviews() {
    reviews = await (await fetch("/api/reviews")).json();
    renderReviews();
  }

  function renderReviews() {
    $("reviewCount").textContent = `${reviews.length}`;
    $("reviewList").innerHTML = reviews
      .slice()
      .reverse()
      .map((r) => {
        const n = Math.max(0, Math.min(5, Math.round(Number(r.rating) || 0)));
        const stars = n ? `<span class="stars" title="${esc(r.rating)} stars">${NG.icon("starFill").repeat(n)}</span>` : "";
        const lang = r.lang && r.lang !== "en" ? `<span class="chip sky">${esc(NG.langName(r.lang))}</span>` : "";
        const en = r.text_en && r.text_en !== r.text ? `<div class="en">EN: ${esc(r.text_en)}</div>` : r.lang && r.lang !== "en" && !r.text_en ? `<div class="en">Translation pending (model loading)</div>` : "";
        return `<div class="review" id="rev-${esc(r.id)}"><div class="meta"><b>${esc(r.id)}</b><span>${stars}</span>${lang}<span>${esc(r.source || "")}</span><span>${esc(r.date || "")}</span></div><div dir="auto">${esc(r.text)}</div>${en}</div>`;
      })
      .join("") || `<p class="muted small">No reviews yet. Paste some above, or try the sample reviews.</p>`;
  }

  async function postReviews(body) {
    let res;
    try {
      res = await fetch("/api/reviews", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    } catch {
      NG.toast("Couldn't reach Guidewire. Is it still running?");
      return false;
    }
    if (!res.ok) {
      let detail = "";
      try {
        detail = (await res.json()).detail;
      } catch {
        /* not JSON */
      }
      NG.toast(detail || "Couldn't add those reviews.");
      return false;
    }
    reviews = await res.json();
    renderReviews();
    NG.toast(`You now have ${reviews.length} reviews`);
    return true;
  }

  $("addReviews").onclick = () => {
    const raw = $("reviewInput").value.trim();
    if (!raw) return;
    postReviews({ raw }).then((added) => added && ($("reviewInput").value = ""));
  };
  $("reviewFile").onchange = async (e) => {
    const file = e.target.files[0];
    if (file) postReviews({ raw: await file.text() });
    e.target.value = "";
  };
  $("sampleReviews").onclick = () => postReviews({ sample: true });
  $("emptySample").onclick = () => postReviews({ sample: true });
  $("clearReviews").onclick = async () => {
    if (!confirm("Remove all reviews? This can't be undone.")) return;
    reviews = await (await fetch("/api/reviews", { method: "DELETE" })).json();
    renderReviews();
  };

  // ---------- Insights ----------
  $("genBtn").onclick = async () => {
    $("genBtn").disabled = true;
    $("genStatus").textContent = "Reading your tours and reviews… this can take a minute.";
    try {
      const res = await fetch("/api/insights", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.recommendations) throw new Error(data.detail || "no tips came back");
      renderInsights(data);
      await loadReviews();
    } catch (err) {
      $("genStatus").textContent = `Couldn't make tips (${err.message}). Please try again.`;
    } finally {
      $("genBtn").disabled = false;
    }
  };

  function evidence(ids) {
    const map = reviewById();
    return `<span class="evidence">${(ids || [])
      .map((id) => {
        const r = map[id];
        const title = r ? r.text_en || r.text : String(id);
        return `<span class="chip ${r ? "olive" : ""}" data-rev="${esc(id)}" title="${esc(title)}">${esc(id)}</span>`;
      })
      .join("")}</span>`;
  }

  function recItems(items, key = "point") {
    if (!items?.length) return `<li class="muted small">Not enough information yet.</li>`;
    return items
      .map((i) => `<li>${esc(i[key] || i.point || i.idea)}${i.why ? `<span class="why">${esc(i.why)}</span>` : ""}${evidence(i.evidence || i.ids)}</li>`)
      .join("");
  }

  function renderInsights(d) {
    if (!d || !d.recommendations) return;
    $("emptyState").classList.add("hidden");
    $("insightsBody").classList.remove("hidden");
    const rec = d.recommendations;
    $("keep").innerHTML = recItems(rec.keep_doing);
    $("fix").innerHTML = recItems(rec.fix);
    $("ideas").innerHTML = recItems(rec.next_products, "idea");
    $("listing").textContent = rec.listing_copy || "";
    $("followUp").textContent = rec.follow_up_message || "";
    $("listing").classList.remove("muted");
    $("followUp").classList.remove("muted");
    const t = d.tours || {};
    const r = d.reviews || {};
    NG.bars($("stopBars"), t.questions_by_stop || []);
    NG.bars($("topicBars"), t.questions_by_topic || [], { labelKey: "label" });
    NG.bars($("praiseBars"), (r.praised || []).map((p) => ({ ...p, id: p.aspect })), { valueKey: "count", labelKey: "aspect" });
    NG.bars($("complaintBars"), (r.complaints || []).map((p) => ({ ...p, id: p.aspect })), { valueKey: "count", labelKey: "aspect", hotId: r.complaints?.[0]?.aspect });
    const kpi = (v, l) => `<div class="kpi"><b>${esc(v ?? "–")}</b><span class="small muted">${esc(l)}</span></div>`;
    const langs = Object.keys(t.guest_languages || {}).join(", ");
    $("kpis").innerHTML =
      kpi(t.tour_count, "tours") + kpi(t.question_count, "guest questions") + kpi(r.review_count, "reviews") + kpi(r.avg_rating, "average stars") + kpi(langs || "–", "guest languages");
    const model = d.source === "llm" ? "Written by the smart assistant" : "Made from your tours and reviews";
    $("genStatus").textContent = `${model} · ${new Date().toLocaleTimeString()}`;
  }

  document.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-rev]");
    if (!chip) return;
    const el = document.getElementById(`rev-${chip.dataset.rev}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 1500);
  });

  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.onclick = async () => {
      try {
        await navigator.clipboard.writeText($(btn.dataset.copy).textContent);
        NG.toast("Copied");
      } catch {
        NG.toast("Copy is not available here");
      }
    };
  });

  // ---------- Tours ----------
  async function loadTours() {
    const tours = await (await fetch("/api/tours")).json();
    $("tourList").innerHTML = tours.length
      ? ""
      : `<p class="muted small">No tours yet. Tours you run will show up here.</p>`;
    for (const t of tours) {
      const b = document.createElement("button");
      b.className = "tour-item";
      const when = t.started_at ? new Date(t.started_at).toLocaleString() : t.id;
      b.innerHTML = `<span>${esc(when)}${t.demo ? ' <span class="chip">demo</span>' : ""}</span><span class="small">${t.question_count} questions · ${esc(t.hot_stop_name || t.hot_stop || "–")}</span>`;
      b.onclick = () => showTour(t.id, b);
      $("tourList").append(b);
    }
  }

  async function showTour(id, btn) {
    document.querySelectorAll(".tour-item").forEach((x) => x.classList.toggle("on", x === btn));
    const tour = await (await fetch(`/api/tours/${encodeURIComponent(id)}`)).json();
    const r = tour.report || {};
    const list = (items) => (items?.length ? `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : `<p class="muted small">None.</p>`);
    $("tourReport").innerHTML = `
      <p class="small muted">${r.question_count ?? 0} questions · ${r.suggestions_used ?? 0}/${r.suggestions ?? 0} suggestions used · languages: ${esc((r.guest_languages || []).map(NG.langName).join(", ") || "–")}</p>
      <div id="tourBars" class="bars"></div>
      <h2 style="margin-top:12px">Recommendations</h2>${list(r.recommendations)}
      ${r.unanswered?.length ? `<h2>Unanswered</h2>${list(r.unanswered)}` : ""}`;
    NG.bars($("tourBars"), r.stops || []);
  }

  await Promise.all([loadReviews(), loadTours()]);
  const last = await (await fetch("/api/insights")).json();
  if (last.recommendations) renderInsights(last);
})();

// ---------- Ask about your business ----------
(() => {
  const $ = (id) => document.getElementById(id);
  const { esc } = NG;
  const examples = [
    "Where are guests most engaged?",
    "What do French guests ask about most?",
    "What should I fix first?",
    "What do visitors love?",
    "Which questions couldn't I answer?",
    "What do people say about the olive press?",
  ];
  for (const ex of examples) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = ex;
    b.onclick = () => ask(ex, "en");
    $("askExamples").append(b);
  }

  $("askForm").onsubmit = (e) => {
    e.preventDefault();
    const q = $("askInput").value.trim();
    if (q) ask(q, null);
  };

  async function ask(question, lang) {
    $("askInput").value = question;
    $("askBtn").disabled = true;
    $("askStatus").textContent = "Looking through your tours and reviews…";
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, lang }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Request failed");
      render(question, data);
      $("askStatus").textContent = "";
      $("askInput").value = "";
    } catch (err) {
      $("askStatus").textContent = `Couldn't answer: ${err.message}`;
    } finally {
      $("askBtn").disabled = false;
    }
  }

  function render(question, d) {
    const card = document.createElement("div");
    card.className = "qa";
    const ev = (d.evidence || [])
      .map((id) => `<span class="chip ${/^R\d+$/.test(id) ? "olive" : ""}" ${/^R\d+$/.test(id) ? `data-rev="${esc(id)}"` : ""}>${esc(id)}</span>`)
      .join("");
    const snips = (d.snippets || []).map((s) => `<li><b>${esc(s.source === "tour" ? "Tour" : s.source)}:</b> <span dir="auto">${esc(s.text)}</span></li>`).join("");
    const translated = d.lang && d.lang !== "en" ? `<div class="a-en">In English: ${esc(d.answer_en)}</div>` : "";
    card.innerHTML = `
      <div class="q" dir="auto">${esc(question)}</div>
      <div class="a" dir="auto">${esc(d.answer)}</div>
      ${translated}
      ${snips ? `<ul class="snips">${snips}</ul>` : ""}
      <div class="meta">${ev}<span class="spacer"></span><span>${d.source === "llm" ? "Written by the smart assistant" : "From your tours and reviews"}</span><button type="button" class="btn ghost sm speak">${NG.icon("volume")}Read aloud</button></div>`;
    card.querySelector(".speak").onclick = () => {
      window.speechSynthesis?.cancel();
      if (!NG.speak(d.answer, d.bcp47)) NG.speak(d.answer_en, "en-US");
    };
    $("askAnswers").prepend(card);
  }

  // Voice: record 16 kHz PCM with the same worklet the tour uses, then let
  // on-device Whisper transcribe it.
  let rec = null;
  let chunks = [];
  let meterRaf = 0;
  let recTimer = 0;
  $("askMic").onclick = async () => {
    if (rec) return stop();
    try {
      chunks = [];
      rec = await NG.startMic((pcm) => chunks.push(new Uint8Array(pcm)));
      $("askMic").classList.add("recording");
      $("askMic").setAttribute("aria-pressed", "true");
      $("askMic").innerHTML = NG.icon("stop");
      const tick = () => {
        const level = Math.round(rec.level() * 100);
        $("askStatus").innerHTML = `<span class="ask-level" style="--level:${level / 100}"></span>Listening… tap the button again when you're done`;
        meterRaf = requestAnimationFrame(tick);
      };
      tick();
      // Hard stop at 30 s.
      recTimer = setTimeout(() => rec && stop(), 30000);
    } catch (err) {
      rec = null;
      $("askStatus").textContent = `Microphone unavailable: ${err.message}`;
    }
  };

  async function stop() {
    clearTimeout(recTimer);
    cancelAnimationFrame(meterRaf);
    rec.stop();
    rec = null;
    $("askMic").classList.remove("recording");
    $("askMic").setAttribute("aria-pressed", "false");
    $("askMic").innerHTML = NG.icon("mic");
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const body = new Uint8Array(total);
    let at = 0;
    for (const c of chunks) {
      body.set(c, at);
      at += c.length;
    }
    $("askStatus").textContent = "Transcribing…";
    try {
      const res = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Transcription failed");
      if (!data.text) {
        $("askStatus").textContent = "I didn't catch that. Try again a little closer to the mic.";
        return;
      }
      ask(data.text, data.lang);
    } catch (err) {
      $("askStatus").textContent = err.message;
    }
  }
})();
