// "My farm": a plain form over the farm briefing (/api/profile). Fields the form doesn't show, such
// as topics and FAQ keywords, are carried over untouched.
(async () => {
  const $ = (id) => document.getElementById(id);
  const { esc } = NG;
  let profile = await (await fetch("/api/profile")).json();
  let dirty = false;
  let rawDirty = false;

  const lines = (text) => text.split("\n").map((t) => t.trim()).filter(Boolean);

  // ---------- Fill ----------
  function fill() {
    document.querySelectorAll("[data-key]").forEach((el) => (el.value = profile[el.dataset.key] ?? ""));
    document.querySelectorAll("[data-lines]").forEach((el) => (el.value = (profile[el.dataset.lines] || []).join("\n")));
    $("stops").innerHTML = "";
    (profile.stops || []).forEach(addStopRow);
    $("products").innerHTML = "";
    (profile.products || []).forEach(addProductRow);
    $("faq").innerHTML = "";
    (profile.faq || []).forEach(addFaqRow);
    $("profileJson").value = JSON.stringify(profile, null, 2);
    rawDirty = false;
    setDirty(false);
  }

  function row(listId, html, data) {
    const el = document.createElement("div");
    el.className = "item";
    el._data = data || {};
    el.innerHTML = `${html}<button type="button" class="btn ghost icon-only remove" aria-label="Remove" title="Remove">${NG.icon("trash")}</button>`;
    el.querySelector(".remove").onclick = () => {
      el.remove();
      renumber();
      setDirty(true);
    };
    $(listId).append(el);
    return el;
  }

  function addStopRow(stop = {}) {
    const el = row(
      "stops",
      `<span class="stop-n"></span>
       <div class="item-fields">
         <label class="field"><span>Stop name</span><input type="text" class="s-name" value="${esc(stop.name || "")}" placeholder="e.g. Olive grove"></label>
         <label class="field"><span>Facts about this stop <small>(one per line)</small></span><textarea class="s-facts" rows="3" placeholder="e.g. We have about 600 olive trees.">${esc((stop.facts || []).join("\n"))}</textarea></label>
       </div>`,
      stop,
    );
    renumber();
    return el;
  }

  function addProductRow(p = {}) {
    return row(
      "products",
      `<div class="item-fields cols">
         <label class="field grow"><span>Product</span><input type="text" class="p-name" value="${esc(p.name || "")}" placeholder="e.g. Olive oil, 1 litre"></label>
         <label class="field price"><span>Price</span><input type="text" class="p-price" value="${esc(p.price || "")}" placeholder="e.g. 14 JOD"></label>
       </div>`,
      p,
    );
  }

  function addFaqRow(f = {}) {
    return row(
      "faq",
      `<div class="item-fields">
         <label class="field"><span>Question</span><input type="text" class="f-q" value="${esc(f.q || "")}" placeholder="e.g. Can I buy the olive oil?"></label>
         <label class="field"><span>Your answer</span><textarea class="f-a" rows="2" placeholder="e.g. Yes, a litre is 14 JOD.">${esc(f.a || "")}</textarea><small class="no-answer">No answer yet. The helper won't use this question until you add one.</small></label>
       </div>`,
      f,
    );
  }

  function renumber() {
    document.querySelectorAll("#stops .stop-n").forEach((n, i) => (n.textContent = i + 1));
  }

  const focusLast = (listId) => $(listId).lastElementChild?.querySelector("input")?.focus();
  $("addStop").onclick = () => (addStopRow(), focusLast("stops"), setDirty(true));
  $("addProduct").onclick = () => (addProductRow(), focusLast("products"), setDirty(true));
  $("addFaq").onclick = () => (addFaqRow(), focusLast("faq"), setDirty(true));

  // ---------- Collect ----------
  const slug = (s) => s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40) || "stop";
  const STOP_WORDS = new Set(["what", "when", "where", "which", "does", "have", "with", "your", "this", "that", "there", "about", "would", "could", "from", "they", "them", "much"]);
  const keywordsFor = (q) => [...new Set(q.toLowerCase().match(/[\p{L}']{4,}/gu) || [])].filter((w) => !STOP_WORDS.has(w)).slice(0, 6);

  // Rows the guide half-filled are not thrown away: they block the save and get highlighted.
  // A completely empty row (e.g. "Add a stop" clicked by mistake) is simply skipped.
  function collect() {
    const p = structuredClone(profile);
    const problems = [];
    const need = (el, message) => problems.push({ el, message });
    document.querySelectorAll("[data-key]").forEach((el) => (p[el.dataset.key] = el.value.trim()));
    document.querySelectorAll("[data-lines]").forEach((el) => (p[el.dataset.lines] = lines(el.value)));

    // Ids are how past tours and the demo refer to stops, so they never change. A stop that
    // was deleted and added back under the same name gets its old id again.
    const oldIds = new Map((profile.stops || []).map((s) => [String(s.name || "").trim().toLowerCase(), s.id]));
    const kept = new Set([...$("stops").children].map((el) => el._data.id).filter(Boolean));
    const used = new Set();
    p.stops = [...$("stops").children]
      .map((el) => {
        const nameEl = el.querySelector(".s-name");
        const name = nameEl.value.trim();
        const facts = lines(el.querySelector(".s-facts").value);
        if (!name) {
          if (facts.length || el._data.id) need(nameEl, "Give every stop a name.");
          return null;
        }
        let id = el._data.id;
        if (!id) {
          const old = oldIds.get(name.toLowerCase());
          id = old && !kept.has(old) && !used.has(old) ? old : slug(name);
        }
        while (used.has(id) || (!el._data.id && kept.has(id) && id !== oldIds.get(name.toLowerCase()))) id += "_2";
        used.add(id);
        return { ...el._data, id, name, facts };
      })
      .filter(Boolean);

    p.products = [...$("products").children]
      .map((el) => {
        const nameEl = el.querySelector(".p-name");
        const name = nameEl.value.trim();
        const price = el.querySelector(".p-price").value.trim();
        if (!name) {
          if (price || el._data.name) need(nameEl, "Give every product a name.");
          return null;
        }
        return { ...el._data, name, price };
      })
      .filter(Boolean);

    p.faq = [...$("faq").children]
      .map((el) => {
        const qEl = el.querySelector(".f-q");
        const q = qEl.value.trim();
        const a = el.querySelector(".f-a").value.trim();
        if (!q) {
          if (a || el._data.q) need(qEl, "Every answer needs its question.");
          return null;
        }
        // Keep hand-picked keywords; add words from an edited question on top.
        const keywords = [...new Set([...(el._data.keywords || []), ...(el._data.q === q ? [] : keywordsFor(q))])];
        return { ...el._data, q, a, keywords: keywords.length ? keywords : keywordsFor(q) };
      })
      .filter(Boolean);
    return { profile: p, problems };
  }

  // ---------- Save ----------
  let formDirty = false;
  function setDirty(on) {
    dirty = on;
    if (!on) formDirty = false;
    $("saveBtn").disabled = !on;
    $("saveBar").classList.toggle("dirty", on);
    $("saveStatus").textContent = on ? "You have unsaved changes" : "All changes saved";
  }

  // The advanced box always mirrors the form until someone types in it.
  const syncRaw = () => {
    if (!rawDirty) $("profileJson").value = JSON.stringify(collect().profile, null, 2);
  };
  document.querySelector(".advanced").addEventListener("toggle", syncRaw);

  $("farmForm").addEventListener("input", (e) => {
    e.target.classList?.remove("invalid");
    if (e.target.id === "profileJson") rawDirty = true;
    else {
      formDirty = true;
      if (document.querySelector(".advanced").open) syncRaw();
    }
    setDirty(true);
  });

  // Enter in a one-line box moves on instead of saving half-typed work.
  $("farmForm").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.tagName === "INPUT") e.preventDefault();
  });

  $("farmForm").onsubmit = async (e) => {
    e.preventDefault();
    document.querySelectorAll(".invalid").forEach((el) => el.classList.remove("invalid"));
    let next;
    if (rawDirty) {
      if (formDirty && !confirm("You changed both the form and the advanced data. Save the advanced data? Your form changes will be lost.")) return;
      try {
        next = JSON.parse($("profileJson").value);
      } catch (err) {
        $("saveStatus").textContent = "The advanced data has a mistake. Fix it or reload the page.";
        return;
      }
    } else {
      const { profile: collected, problems } = collect();
      if (problems.length) {
        problems.forEach((x) => x.el.classList.add("invalid"));
        problems[0].el.scrollIntoView({ behavior: "smooth", block: "center" });
        problems[0].el.focus({ preventScroll: true });
        $("saveStatus").textContent = problems[0].message;
        return;
      }
      next = collected;
    }
    if (!next.stops?.length) {
      NG.toast("Add at least one tour stop first.");
      return;
    }
    $("saveBtn").disabled = true;
    $("saveStatus").textContent = "Saving…";
    let res;
    try {
      res = await fetch("/api/profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(next) });
    } catch {
      res = null;
    }
    if (!res?.ok) {
      $("saveStatus").textContent = res ? "Couldn't save. Please try again." : "Couldn't reach Guidewire. Is it still running?";
      $("saveBtn").disabled = false;
      return;
    }
    profile = await res.json();
    fill();
    NG.toast("Saved. Your next tour will use this.");
  };

  window.addEventListener("beforeunload", (e) => {
    if (dirty) e.preventDefault();
  });

  fill();
})();
