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

/* What this store actually sells, read off the catalogue rather than written
   down here, so it stays true when the catalogue changes. */
function categoryList() {
  const seen = [];
  for (const p of state.products) {
    const c = (p.category || "").toLowerCase();
    if (c && !seen.includes(c)) seen.push(c);
  }
  if (!seen.length) return "phone and desk accessories";
  if (seen.length === 1) return seen[0];
  // Naming all eight ends on the awkward ones ("...speakers, desk and cases").
  // Five and a tail reads like a shop assistant rather than a database dump.
  if (seen.length > 5) return `${seen.slice(0, 5).join(", ")} and more`;
  return `${seen.slice(0, -1).join(", ")} and ${seen[seen.length - 1]}`;
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

  // No product chosen means the catalogue had no answer. Showing the shelf
  // anyway is worse than showing nothing -- sixteen chargers under "we have no
  // phones" reads as a broken page. Skip the rail and the checkout card, and
  // say plainly what the store does carry.
  if (d.product_id && d.product && d.product.title) {
    body.appendChild(rail(state.products, held, d.product_id));
    await wait(200);
    await checkout(res, body);
  } else {
    body.appendChild(el("div", "no-match",
      `<strong>Sorry — we don't stock that.</strong>
       <span>Nothing in this catalogue answered the request, so nothing was added
       to the cart. This store carries ${esc(categoryList())}.</span>`));
  }
  await wait(160);

  body.appendChild(auditPanel(res));
}

function column(kind, res) {
  const c = el("div", `col col-${kind}`);
  const withheld = (res.findings || []).filter((f) => f.verdict === "block").length;
  const flagged = (res.findings || []).filter((f) => f.verdict === "flag").length;
  const on = [withheld ? `${withheld} withheld` : "", flagged ? `${flagged} sanitised` : ""]
    .filter(Boolean).join(" · ") || "nothing withheld";
  c.innerHTML = `
    <div class="col-h">
      <span class="col-tag ${kind}">Sentr ${kind}</span>
      <span class="col-sub">${kind === "off" ? "every listing reached the assistant" : on}</span>
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

/* ---------------- evidence panel ----------------
   The numbers come from /api/metrics, which reads eval/results/. Nothing here
   is typed in by hand, so the page cannot drift away from the committed
   claims it is summarising. */
const pct = (v) => (v == null ? "—" : `${v}%`);

async function evidencePanel() {
  const m = await fetch("/api/metrics").then((r) => r.json());
  const p = el("section", "evidence");
  if (!m.available) {
    p.innerHTML = `<div class="ev-h"><span class="ev-t">Evidence</span></div>
      <div class="ev-body"><p class="ev-note">${esc(m.why || "results not found")}</p></div>`;
    return p;
  }

  const row = (label, a, b, hint) => `
    <tr><td>${esc(label)}${hint ? `<span class="hint">${esc(hint)}</span>` : ""}</td>
      <td class="num win">${a}</td><td class="num">${b}</td></tr>`;

  const cost = m.cost && m.cost.detectors ? m.cost.detectors : [];
  const ours = cost[0], theirs = cost[1];

  p.innerHTML = `
    <div class="ev-h">
      <span class="ev-t">Held-out results</span>
      <span class="ev-m">${m.n.toLocaleString("en-IN")} listings · ${m.benign.toLocaleString("en-IN")} real, ${m.poisoned} poisoned</span>
    </div>
    <div class="ev-body">
      <p class="ev-lede">Split off before any detection code was written, and opened
        <strong>exactly ${m.opened_times === 1 ? "once" : `${m.opened_times} times`}</strong>
        — at commit <code>${esc(m.commit)}</code>. Nothing was tuned afterwards.</p>
      <div class="tbl-wrap">
        <table class="ev-tbl">
          <thead><tr><th></th><th>Sentr</th><th>Public guardrail</th></tr></thead>
          <tbody>
            ${row("Attacks detected", pct(m.sentr.recall_pct), pct(m.baseline?.recall_pct))}
            ${row("Attacks neutralised", pct(m.sentr.neutralised_pct), pct(m.baseline?.neutralised_pct),
                  "payload never reaches the agent")}
            ${row("Honest listings blocked",
                  `${m.sentr.blocked_false_positives} of ${m.benign.toLocaleString("en-IN")}`,
                  `${m.baseline ? m.baseline.blocked_false_positives : "—"} of ${m.benign.toLocaleString("en-IN")}`,
                  "the only false positive that costs money")}
            ${row("Precision", pct(m.sentr.precision_pct), pct(m.baseline?.precision_pct))}
            ${row("Throughput",
                  m.sentr.throughput ? `${m.sentr.throughput}/sec` : "—",
                  m.baseline?.throughput ? `${m.baseline.throughput}/sec` : "not recorded")}
            ${ours && theirs ? row("False positives, per month",
                  `₹${ours.monthly_inr.toLocaleString("en-IN")}`,
                  `₹${theirs.monthly_inr.toLocaleString("en-IN")}`,
                  "at the stated assumptions") : ""}
          </tbody>
        </table>
      </div>
      ${ours ? `<p class="ev-note"><strong>On the money figure:</strong> zero false
        positives in ${m.benign.toLocaleString("en-IN")} listings is not a zero rate, so the
        pessimistic bound is published too — up to ₹${ours.monthly_upper_inr.toLocaleString("en-IN")}
        a month at the 95% interval. Assumptions:
        ${m.cost.assumptions.listings.toLocaleString("en-IN")} listings/month ×
        ${m.cost.assumptions.conversion_rate} conversion ×
        ₹${m.cost.assumptions.average_order_value_inr.toLocaleString("en-IN")} order value.</p>` : ""}
      ${(m.corrections || []).map((c) => `<p class="ev-note warn"><strong>Correction:</strong> ${esc(c)}</p>`).join("")}
      <p class="ev-src">Read live from <code>eval/results/day5_final.json</code> and
        <code>cost_model.json</code> — the committed files that hold these claims.</p>
    </div>`;
  return p;
}

/* ---------------- screen-your-own panel ----------------
   The same pipeline the catalogue runs through. The point is that a visitor can
   check the verdict on text they wrote themselves, which is the only way to
   tell a real detector from a rehearsed one. */
function screenPanel() {
  const p = el("section", "screener");
  p.innerHTML = `
    <div class="ev-h">
      <span class="ev-t">Screen a listing</span>
      <span class="ev-m">same pipeline, no demo mode</span>
    </div>
    <div class="ev-body">
      <p class="ev-lede">Write a product description — honest or hostile — and see what
        Sentr does with it. Every verdict points at the exact characters that caused it.</p>
      <label class="fld"><span>Title</span>
        <input id="scTitle" type="text" autocomplete="off"
               value="TrailMate 20000mAh Power Bank"></label>
      <label class="fld"><span>Description</span>
        <textarea id="scDesc" rows="5">20000mAh power bank with 65W USB-C output, enough to charge a laptop. Recharges in 2 hours. Airline safe.</textarea></label>
      <div class="fld-row">
        <button class="scBtn" id="scGo" type="button">Screen it</button>
        <span class="fld-hint" id="scHint">Rules run in about a millisecond.</span>
      </div>
      <div id="scOut"></div>
    </div>`;

  const out = () => p.querySelector("#scOut");
  p.querySelector("#scGo").onclick = async () => {
    const btn = p.querySelector("#scGo");
    btn.disabled = true;
    out().innerHTML = `<p class="ev-note">Screening…</p>`;
    let r;
    try {
      r = await fetch("/api/screen", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: p.querySelector("#scTitle").value,
          description: p.querySelector("#scDesc").value,
        }),
      }).then((x) => x.json());
    } catch {
      out().innerHTML = `<p class="ev-note warn">The server stopped responding.</p>`;
      btn.disabled = false;
      return;
    }
    btn.disabled = false;
    if (r.error) {
      out().innerHTML = `<p class="ev-note warn">${esc(r.error)}</p>`;
      return;
    }

    const verdictLine = {
      block: "Withheld. This never reaches the buying agent.",
      flag: "Passed through sanitised, and logged for review — the listing still sells.",
      allow: "Clean. Passed through unchanged.",
    }[r.verdict];

    const trig = (r.triggers || []).map((t) => `
      <div class="trig"><span class="trig-rule">${esc(t.layer)} · ${esc(t.rule_id)}</span>
        <code>${esc(t.span)}</code></div>`).join("");

    out().innerHTML = `
      <div class="sc-res ${esc(r.verdict)}">
        <div class="sc-h">
          <span class="v-badge ${esc(r.verdict)}">${esc(r.verdict)}</span>
          <span class="sc-say">${esc(verdictLine)}</span>
        </div>
        <div class="sc-meta">
          decided by <strong>${esc(r.decided_by)}</strong> ·
          confidence ${r.confidence} ·
          ${r.chars} characters in ${r.latency_ms} ms
        </div>
        ${trig ? `<div class="trigs">${trig}</div>`
               : `<p class="ev-note">No rule fired. On a clean listing that is the
                   correct outcome — and it is also what 13.1% of attacks get,
                   which the README does not hide.</p>`}
        ${r.sanitized ? `<div class="diff">
            <div class="diff-h">What the agent would have read, after sanitising</div>
            <pre class="inspect">${esc(r.text_after)}</pre>
            ${(r.sanitiser_notes || []).map((n) => `<p class="ev-note">${esc(n)}</p>`).join("")}
          </div>` : ""}
      </div>`;
  };
  return p;
}

/* ---------------- panel plumbing ---------------- */
async function showPanel(kind) {
  if (state.busy) return;
  document.querySelector(".intro")?.remove();
  const t = turn("bot");
  t.appendChild(el("div", "status", `<span class="spinner"></span><span>Loading…</span>`));
  const node = kind === "evidence" ? await evidencePanel() : screenPanel();
  t.innerHTML = "";
  t.appendChild(node);
  toBottom();
}

/* ---------------- wiring ---------------- */
$("composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
$("btnEvidence").addEventListener("click", () => showPanel("evidence"));
$("btnScreen").addEventListener("click", () => showPanel("screen"));

boot();
