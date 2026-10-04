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
        const stars = r.rating ? "★".repeat(Math.round(r.rating)) : "";
        const lang = r.lang && r.lang !== "en" ? `<span class="chip sky">${esc(NG.langName(r.lang))}</span>` : "";
        const en = r.text_en && r.text_en !== r.text ? `<div class="en">EN: ${esc(r.text_en)}</div>` : r.lang && r.lang !== "en" && !r.text_en ? `<div class="en">Translation pending (model loading)</div>` : "";
        return `<div class="review" id="rev-${esc(r.id)}"><div class="meta"><b>${esc(r.id)}</b><span>${stars}</span>${lang}<span>${esc(r.source || "")}</span><span>${esc(r.date || "")}</span></div><div dir="auto">${esc(r.text)}</div>${en}</div>`;
      })
      .join("") || `<p class="muted small">No reviews yet. Paste some, upload a CSV, or load the samples.</p>`;
  }

  async function postReviews(body) {
    const res = await fetch("/api/reviews", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) {
      NG.toast((await res.json()).detail || "Could not add reviews");
      return;
    }
    reviews = await res.json();
    renderReviews();
    NG.toast(`${reviews.length} reviews`);
  }

  $("addReviews").onclick = () => {
    const raw = $("reviewInput").value.trim();
    if (!raw) return;
    postReviews({ raw }).then(() => ($("reviewInput").value = ""));
  };
  $("reviewFile").onchange = async (e) => {
    const file = e.target.files[0];
    if (file) postReviews({ raw: await file.text() });
    e.target.value = "";
  };
  $("sampleReviews").onclick = () => postReviews({ sample: true });
  $("clearReviews").onclick = async () => {
    if (!confirm("Remove all imported reviews?")) return;
    reviews = await (await fetch("/api/reviews", { method: "DELETE" })).json();
    renderReviews();
  };

  // ---------- Insights ----------
  $("genBtn").onclick = async () => {
    $("genBtn").disabled = true;
    $("genStatus").textContent = "Reading tours and reviews… (a small model on CPU can take a minute)";
    try {
      const res = await fetch("/api/insights", { method: "POST" });
      const data = await res.json();
      renderInsights(data);
      await loadReviews();
    } catch (err) {
      $("genStatus").textContent = `Failed: ${err.message}`;
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
    if (!items?.length) return `<li class="muted small">Not enough data yet.</li>`;
    return items
      .map((i) => `<li>${esc(i[key] || i.point || i.idea)}${i.why ? `<span class="why">${esc(i.why)}</span>` : ""}${evidence(i.evidence || i.ids)}</li>`)
      .join("");
  }

  function renderInsights(d) {
    if (!d || !d.recommendations) return;
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
      kpi(t.tour_count, "tours") + kpi(t.question_count, "visitor questions") + kpi(r.review_count, "reviews") + kpi(r.avg_rating, "avg rating") + kpi(langs || "–", "guest languages");
    const model = d.source === "llm" ? `Written by ${d.model}` : "Rule-based (start Ollama for model-written advice)";
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
      : `<p class="muted small">No tours yet. Run one from the guide console (try the demo tour).</p>`;
    for (const t of tours) {
      const b = document.createElement("button");
      b.className = "tour-item";
      const when = t.started_at ? new Date(t.started_at).toLocaleString() : t.id;
      b.innerHTML = `<span>${esc(when)}${t.demo ? ' <span class="chip">demo</span>' : ""}</span><span class="small muted">${t.question_count} q · ${esc(t.hot_stop || "–")}</span>`;
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

  // ---------- Briefing ----------
  async function loadProfile() {
    const p = await (await fetch("/api/profile")).json();
    $("profileJson").value = JSON.stringify(p, null, 2);
  }
  $("saveProfile").onclick = async () => {
    let parsed;
    try {
      parsed = JSON.parse($("profileJson").value);
    } catch (err) {
      $("profileStatus").textContent = `Not valid JSON: ${err.message}`;
      return;
    }
    const res = await fetch("/api/profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(parsed) });
    $("profileStatus").textContent = res.ok ? "Saved. New tours use it." : (await res.json()).detail;
  };

  await Promise.all([loadReviews(), loadTours(), loadProfile()]);
  const last = await (await fetch("/api/insights")).json();
  if (last.recommendations) renderInsights(last);
})();
