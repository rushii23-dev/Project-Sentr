/* Shopping assistant + Sentr -- front end.

   One request, run twice: once with Sentr off and once with it on, rendered
   side by side. The comparison IS the demo (CLAUDE.md section 12) -- a judge
   should be able to see both totals without scrolling between them.

   The audit record is not hidden behind a disclosure triangle. Track 2 asks for
   an audit trail, so it is rendered as a panel with the triggering spans shown
   in place, and the raw JSON is one click away for anyone who wants it. */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
const rupees = (n) =>
  "Rs " + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const wait = (ms) => new Promise((r) => setTimeout(r, reduced ? 0 : ms));

const state = { busy: false, products: [], runs: {} };

/* ---------------- boot ---------------- */
async function boot() {
  const [cat, status] = await Promise.all([
    fetch("/api/catalog?poisoned=true").then((r) => r.json()),
    fetch("/api/status").then((r) => r.json()),
  ]);
  state.products = cat.products;

  // Deliberately not naming providers or services on screen. Which model
  // answers is an implementation detail, and a footer listing vendor names
  // reads like a credits roll rather than a storefront. Whether a real model
  // was called at all is still disclosed -- in the reply itself, where it
  // actually matters (see `is_evidence` below).
  $("statusNote").textContent = status.razorpay
    ? "Razorpay test mode — no real money moves."
    : "Orders are simulated — no real money moves.";

  document.querySelectorAll(".chip").forEach((c) =>
    c.addEventListener("click", () => {
      $("q").value = c.textContent.trim();
      send();
    })
  );
}

/* ---------------- thread helpers ---------------- */
function turn(kind) {
  const t = el("div", `turn ${kind}`);
  $("thread").appendChild(t);
  return t;
}
const toBottom = () => $("thread").scrollTo({ top: $("thread").scrollHeight, behavior: "smooth" });

/* Reveal `text` over a fixed wall-clock duration rather than a fixed delay per
   character. A per-character sleep balloons to minutes when the browser
   throttles timers (background tab, low memory), which would strand the demo
   mid-sentence. Time-based means it always finishes in about `ms`. */
function type(node, text, ms = 850) {
  if (reduced || !text) { node.textContent = text; return Promise.resolve(); }
  const caret = el("span", "caret");
  node.parentNode.insertBefore(caret, node.nextSibling);
  const t0 = performance.now();
  return new Promise((resolve) => {
    const done = () => { node.textContent = text; caret.remove(); resolve(); };
    const tick = () => {
      const p = Math.min(1, (performance.now() - t0) / ms);
      node.textContent = text.slice(0, Math.round(text.length * p));
      if (p < 1) requestAnimationFrame(tick);
      else done();
    };
    requestAnimationFrame(tick);
    setTimeout(done, ms + 1200); // hard stop if rAF is starved
  });
}

/* ---------------- product rail ---------------- */
function rail(products, heldIds, chosenId) {
  const r = el("div", "rail");
  products.forEach((p, i) => {
    const held = heldIds.has(p.id);
    const c = el("article", `pc${held ? " held" : ""}${p.id === chosenId ? " chosen" : ""}`);
    c.dataset.id = p.id;
    c.style.animationDelay = `${i * 40}ms`;
    c.innerHTML = `
      <div class="pc-img"><img src="${p.image}" alt="${esc(p.title)}" loading="lazy"></div>
      <div class="pc-b">
        <div class="pc-t">${esc(p.title)}</div>
        <div class="pc-p"><span class="n">${rupees(p.price_inr)}</span>
          ${p.mrp_inr ? `<span class="m">${rupees(p.mrp_inr)}</span>` : ""}</div>
        <div class="pc-r">${p.rating} ★ · ${Number(p.reviews).toLocaleString("en-IN")}</div>
        ${held ? `<span class="held-tag">withheld by Sentr</span>` : ""}
      </div>`;
    r.appendChild(c);
  });
  return r;
}

/* ---------------- audit record ---------------- */
/* Rendered open, in place. Three views: the decisions themselves, the exact
   text that reached the model, and the raw JSONL record the pipeline wrote. */
function auditPanel(res) {
  const caught = (res.findings || []).filter((f) => f.verdict !== "allow");
  const recs = res.audit || [];
  const lat = recs.length
    ? (recs.reduce((a, r) => a + (r.latency_ms || 0), 0) / recs.length).toFixed(2)
    : null;
  const version = recs.length ? recs[0].sentr_version : null;

  const panel = el("section", "audit");

  const head = el("div", "audit-h");
  head.innerHTML = `
    <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M12 3l7 3v6c0 4.4-3 8.3-7 9-4-.7-7-4.6-7-9V6l7-3z" stroke-linejoin="round"/>
    </svg>
    <span class="audit-t">Audit record</span>
    ${lat ? `<span class="audit-m">${recs.length} decisions · ${lat} ms each</span>` : ""}`;
  panel.appendChild(head);

  if (!res.sentr_enabled) {
    panel.classList.add("empty");
    panel.appendChild(el("p", "audit-none",
      "Sentr was off. Nothing was screened and nothing was logged — every listing "
      + "reached the assistant exactly as the merchant wrote it."));
    return panel;
  }

  const tabs = el("div", "tabs");
  const tD = el("button", "tab", "Decisions");
  const tC = el("button", "tab", "What the assistant read");
  const tJ = el("button", "tab", "Raw JSON");
  tabs.append(tD, tC, tJ);
  panel.appendChild(tabs);

  const body = el("div", "audit-body");
  panel.appendChild(body);

  /* --- view 1: the decisions, human readable --- */
  const decisions = () => {
    body.className = "audit-body";
    body.innerHTML = "";
    if (!caught.length) {
      body.appendChild(el("p", "audit-none",
        `Sentr screened ${res.screened} listings and found nothing to act on. `
        + "On a clean catalogue that is the correct outcome."));
      return;
    }
    caught.forEach((f) => {
      const row = el("div", `dec ${f.verdict}`);
      row.innerHTML = `
        <div class="dec-h">
          <span class="v-badge ${f.verdict}">${esc(f.verdict)}</span>
          <span class="dec-id">${esc(f.listing_id)}</span>
          <span class="dec-by">decided by ${esc(f.decided_by)}</span>
          <span class="dec-c">confidence ${Number(f.confidence).toFixed(2)}</span>
        </div>`;
      const trig = el("div", "trigs");
      (f.triggers || []).forEach((t) => {
        trig.appendChild(el("div", "trig",
          `<span class="trig-rule">${esc(t.layer)} · ${esc(t.rule_id)}</span>
           <code>${esc(t.span)}</code>`));
      });
      row.appendChild(trig);
      body.appendChild(row);
    });
    const foot = el("p", "audit-foot",
      `Every verdict points at the exact characters that caused it. `
      + `${version ? esc(version) : ""}`);
    body.appendChild(foot);
  };

  /* --- view 2: the text that actually reached the model --- */
  const spans = (res.known_injections || []).map((k) => k.span).filter(Boolean);
  const context = () => {
    body.className = "audit-body";
    let h = esc(res.agent_context || "");
    spans.forEach((s) => { h = h.split(esc(s)).join(`<mark>${esc(s)}</mark>`); });
    body.innerHTML = `<pre class="inspect">${h}</pre>`;
    const hit = spans.some((s) => (res.agent_context || "").includes(s));
    body.appendChild(el("p", "audit-foot", hit
      ? "Highlighted: the injected instruction, present in what the model read."
      : "The injected instruction is absent — it never reached the model."));
  };

  /* --- view 3: the raw record --- */
  const raw = () => {
    body.className = "audit-body";
    body.innerHTML = `<pre class="inspect">${esc(JSON.stringify(recs, null, 2) || "[]")}</pre>`;
    body.appendChild(el("p", "audit-foot",
      "Exactly what the pipeline appended to eval/results/audit_run_on.jsonl."));
  };

  const show = (which) => {
    tD.setAttribute("aria-selected", String(which === "d"));
    tC.setAttribute("aria-selected", String(which === "c"));
    tJ.setAttribute("aria-selected", String(which === "j"));
    ({ d: decisions, c: context, j: raw }[which])();
  };
  tD.onclick = () => show("d");
  tC.onclick = () => show("c");
  tJ.onclick = () => show("j");
  show("d");

  return panel;
}

/* ---------------- checkout card ---------------- */
async function checkout(res, mount) {
  const d = res.decision;
  const card = el("div", "co");
  card.innerHTML = `
    <div class="co-h"><span>Checkout</span><span class="seller">Kirana Tech · Razorpay</span></div>
    <div class="co-lines"></div>`;
  const lines = card.querySelector(".co-lines");
  const foot = el("div", "co-f");
  card.appendChild(foot);
  mount.appendChild(card);

  const add = (t, s, v, cls = "") => {
    lines.appendChild(el("div", `co-line ${cls}`,
      `<span class="t">${esc(t)}${s ? `<span class="s">${esc(s)}</span>` : ""}</span>
       <span class="v">${v}</span>`));
  };

  if (d.product && d.product.title) {
    add(d.product.title, `Qty ${d.quantity}`, rupees(d.base_inr));
    await wait(380);
  }
  for (const a of d.addons || []) {
    add(a.name || "Add-on", "added by the assistant — you did not approve this",
        rupees(a.price_inr), "bad");
    await wait(280);
  }

  const bad = (d.addons || []).length > 0;
  foot.innerHTML = `
    <div class="co-tot"><span>Total</span>
      <span class="amt${bad ? " bad" : ""}">${rupees(d.total_inr)}</span></div>
    <div class="note ${bad ? "bad" : "ok"}">${
      bad
        ? `You asked to stay under Rs 1,500. This order is ${rupees(d.total_inr)} — ${rupees(
            d.addon_total_inr)} of it was added by the assistant, not by you.`
        : "Nothing was added that you did not ask for."
    }</div>
    <button class="pay">Pay with Razorpay</button>
    <div class="order" hidden></div>`;

  const btn = foot.querySelector(".pay");
  const out = foot.querySelector(".order");
  btn.onclick = () => {
    const o = res.order;
    out.hidden = false;
    out.innerHTML = o.simulated
      ? `${esc(o.order_id)}<br>${rupees(o.amount_inr)} · SIMULATED<br>${esc(o.error || "")}`
      : `${esc(o.order_id)}<br>${rupees(o.amount_inr)} · status ${esc(o.status)}<br>Razorpay test mode — no real money moved.`;
    btn.disabled = true;
    btn.textContent = "Payment created";
  };
}

/* ---------------- one column ---------------- */
async function renderRun(col, res) {
  const body = col.querySelector(".col-body");
  const d = res.decision;
  const held = new Set((res.findings || [])
    .filter((f) => f.verdict === "block").map((f) => f.listing_id));

  const say = el("div", "say");
  body.appendChild(say);

  let text = d.reasoning || "Here is what I found.";
  if (d.addons.length) {
    text += `\n\nI've added the ${d.addons[0].name} for ${rupees(d.addons[0].price_inr)}.`;
  }
  if (!d.is_evidence) {
    text += `\n\n(Scripted fallback — no language model was called, so this is not evidence of model behaviour.)`;
  }
  await type(say, text, 700);

  body.appendChild(rail(state.products, held, d.product_id));
  await wait(200);

  await checkout(res, body);
  await wait(160);

  body.appendChild(auditPanel(res));
}

function column(kind, res) {
  const c = el("div", `col col-${kind}`);
  const withheld = (res.findings || []).filter((f) => f.verdict === "block").length;
  c.innerHTML = `
    <div class="col-h">
      <span class="col-tag ${kind}">Sentr ${kind}</span>
      <span class="col-sub">${
        kind === "off"
          ? "every listing reached the assistant"
          : withheld
            ? `${withheld} listing${withheld > 1 ? "s" : ""} withheld`
            : "nothing withheld"
      }</span>
    </div>
    <div class="col-body"></div>`;
  return c;
}

/* ---------------- send ---------------- */
async function run(sentrEnabled, q) {
  return fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request: q, sentr_enabled: sentrEnabled, poisoned: true }),
  }).then((r) => r.json());
}

async function send() {
  if (state.busy) return;
  const q = $("q").value.trim();
  if (!q) return;

  state.busy = true;
  $("send").disabled = true;
  $("q").value = "";
  document.querySelector(".intro")?.remove();

  turn("user").appendChild(el("div", "bubble", esc(q)));
  toBottom();

  const bot = turn("bot");
  const status = el("div", "status",
    `<span class="spinner"></span><span>Running the same request twice — with Sentr off, then on…</span>`);
  bot.appendChild(status);
  toBottom();

  let off, on;
  try {
    // Sequential, not parallel: on a cache miss two concurrent LLM calls can
    // trip a free-tier rate limit, and losing the demo to a 429 is not worth
    // the second saved.
    off = await run(false, q);
    on = await run(true, q);
  } catch {
    status.remove();
    bot.appendChild(el("div", "say", "The demo server stopped responding. Restart it and try again."));
    state.busy = false; $("send").disabled = false;
    return;
  }

  status.remove();
  state.runs = { off, on };

  const grid = el("div", "compare");
  const colOff = column("off", off);
  const colOn = column("on", on);
  grid.append(colOff, colOn);
  bot.appendChild(grid);
  toBottom();

  // Left first, then right: the attack lands before the defence answers it, and
  // both stay on screen afterwards.
  await renderRun(colOff, off);
  await wait(260);
  await renderRun(colOn, on);

  // The unauthorised amount is the add-on the injection forced, NOT the
  // difference between the two totals -- the two runs often buy different
  // products, so that difference mixes "chose something else" with "was made
  // to pay for something nobody approved". Only the second is the attack.
  const forced = off.decision.addon_total_inr || 0;
  if (forced > 0) {
    bot.appendChild(el("div", "verdict-line",
      `Same request, same catalogue. Sentr off pays <strong>${rupees(off.decision.total_inr)}</strong>; `
      + `Sentr on pays <strong>${rupees(on.decision.total_inr)}</strong>. `
      + `<span class="d">${rupees(forced)} of the unprotected total is an add-on the buyer never approved.</span>`));
  }

  // Say what this catalogue is before anyone has to ask. The products here are
  // invented so the attack is never attributed to a real merchant; the measured
  // rates come from real listings, and conflating the two would be the kind of
  // quiet overclaim this project is meant to argue against.
  bot.appendChild(el("div", "provenance",
    "These six products are illustrative, so the poisoned listing is not attributed to a real "
    + "merchant. Sentr's measured false-positive and recall rates come from 6,000 genuine "
    + "Amazon.in and Flipkart listings — see the README."));
  toBottom();

  state.busy = false;
  $("send").disabled = false;
  $("q").focus();
}

/* ---------------- wiring ---------------- */
$("composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });

boot();
