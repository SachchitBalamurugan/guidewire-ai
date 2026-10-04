// A quiet farm horizon behind the page: two rolling hills with olive trees and grass that sway, plus
// a few olive leaves drifting down. Decorative only. <body data-garden="full"> adds the leaves;
// "field" draws just the horizon. Motion stops for prefers-reduced-motion (see garden.css).
(() => {
  const mode = document.body.dataset.garden;
  if (!mode) return;
  const NS = "http://www.w3.org/2000/svg";
  const W = 1440;
  const H = 200;

  // Smooth, deterministic hills, so trees can sit exactly on the ridge.
  const back = (x) => 112 + 20 * Math.sin(x / 300 + 2.1) + 9 * Math.sin(x / 113 + 0.4);
  const front = (x) => 150 + 16 * Math.sin(x / 240 + 0.3) + 7 * Math.sin(x / 91 + 1.7);
  const hillPath = (f) => {
    let d = `M0 ${H} L0 ${f(0).toFixed(1)}`;
    for (let x = 24; x <= W; x += 24) d += ` L${x} ${f(x).toFixed(1)}`;
    return `${d} L${W} ${H} Z`;
  };

  // Seeded random, so the scene looks the same on every page and every load.
  let seed = 7;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);

  // An olive tree: a short leaning trunk and a wide canopy of soft overlapping blobs.
  function tree(x, ground, size, cls) {
    const lean = (rand() - 0.5) * 6;
    const t = size;
    const canopy = [
      [0, -t * 1.05, t * 0.62, t * 0.4],
      [-t * 0.5, -t * 0.85, t * 0.46, t * 0.32],
      [t * 0.52, -t * 0.88, t * 0.44, t * 0.3],
      [t * 0.1, -t * 1.32, t * 0.38, t * 0.26],
    ]
      .map(([cx, cy, rx, ry]) => `<ellipse cx="${(cx + lean).toFixed(1)}" cy="${cy.toFixed(1)}" rx="${rx.toFixed(1)}" ry="${ry.toFixed(1)}"/>`)
      .join("");
    const trunk = `<path d="M${-t * 0.07} 0 C${-t * 0.05} ${-t * 0.35} ${lean - t * 0.08} ${-t * 0.55} ${lean} ${-t * 0.85} L${lean + t * 0.08} ${-t * 0.85} C${lean} ${-t * 0.55} ${t * 0.08} ${-t * 0.3} ${t * 0.09} 0 Z"/>`;
    const delay = (-rand() * 8).toFixed(2);
    return `<g class="g-tree ${cls}" transform="translate(${x.toFixed(1)} ${(ground + 2).toFixed(1)})"><g class="g-sway" style="animation-delay:${delay}s">${trunk}${canopy}</g></g>`;
  }

  function grass(x, ground) {
    const h = 6 + rand() * 9;
    const bend = (rand() - 0.5) * 6;
    const delay = (-rand() * 5).toFixed(2);
    return `<path class="g-blade" style="animation-delay:${delay}s" d="M${x.toFixed(1)} ${ground + 1} q${(bend / 2).toFixed(1)} ${(-h / 2).toFixed(1)} ${bend.toFixed(1)} ${(-h).toFixed(1)}"/>`;
  }

  let trees = "";
  for (const x of [90, 260, 410, 640, 820, 1010, 1220, 1360]) trees += tree(x + rand() * 40, back(x), 15 + rand() * 6, "far");
  let near = "";
  for (const x of [170, 520, 930, 1290]) near += tree(x + rand() * 50, front(x), 22 + rand() * 7, "near");
  let blades = "";
  for (let x = 6; x < W; x += 9 + rand() * 14) blades += grass(x, front(x));

  const field = document.createElement("div");
  field.className = "garden-field";
  field.setAttribute("aria-hidden", "true");
  field.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMax slice" xmlns="${NS}">
      <path class="g-hill back" d="${hillPath(back)}"/>
      ${trees}
      <path class="g-hill front" d="${hillPath(front)}"/>
      ${near}
      <g class="g-grass">${blades}</g>
    </svg>`;
  document.body.prepend(field);

  if (mode !== "full") return;

  // A handful of olive leaves, drifting slowly across the page.
  const leafSvg = `<svg viewBox="0 0 24 24"><path d="M3 21C3 11 10 4 21 3c-1 11-8 18-18 18z"/><path d="M3 21 13 11" class="vein"/></svg>`;
  const sky = document.createElement("div");
  sky.className = "garden-leaves";
  sky.setAttribute("aria-hidden", "true");
  const count = window.innerWidth < 640 ? 4 : 7;
  for (let i = 0; i < count; i++) {
    const leaf = document.createElement("span");
    leaf.className = "g-leaf";
    leaf.innerHTML = leafSvg;
    const dur = 22 + rand() * 18;
    leaf.style.left = `${(i / count) * 100 + rand() * 10}%`;
    leaf.style.setProperty("--size", `${10 + rand() * 9}px`);
    leaf.style.setProperty("--drift", `${(rand() - 0.3) * 160}px`);
    leaf.style.setProperty("--spin", `${(rand() > 0.5 ? 1 : -1) * (200 + rand() * 260)}deg`);
    leaf.style.animationDuration = `${dur}s`;
    leaf.style.animationDelay = `${-rand() * dur}s`;
    sky.append(leaf);
  }
  document.body.prepend(sky);
})();
