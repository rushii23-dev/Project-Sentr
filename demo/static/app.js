/* Shopping assistant + Sentr -- front end.
   One conversation. The assistant answers, shows what it found, and builds a
   checkout card. Sentr's result rides underneath as a quiet expandable chip,
   with an inspector showing the exact text that reached the model. */

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

const state = { sentr: false, busy: false, products: [], runs: {} };

/* ---------------- boot ---------------- */
async function boot() {
  const [cat, status] = await Promise.all([
    fetch("/api/catalog?poisoned=true").then((r) => r.json()),
    fetch("/api/status").then((r) => r.json()),
  ]);
  state.products = cat.products;

  const p = status.providers || {};
  const live = p.groq ? "Groq" : p.gemini ? "Gemini" : null;
  $("modelBadge").textContent = live ? `· ${live}` : "· scripted (no LLM key)";

  const bits = [];
  bits.push(p.groq ? "Groq connected" : "Groq: no key");
  bits.push(p.gemini ? "Gemini connected" : "Gemini: no key");
  bits.push(status.razorpay ? "Razorpay test mode" : "Razorpay: orders simulated");
  const names = [...new Set((status.photo_credits || []).map((c) => c.photographer))];
  $("statusNote").textContent =
    bits.join(" · ") + (names.length ? ` · photos via Pexels: ${names.join(", ")}` : "");

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
    const done = () => { node.textContent = text; caret.remove(); toBottom(); resolve(); };
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
function rail(products, heldIds) {
  const r = el("div", "rail");
  products.forEach((p, i) => {
    const held = heldIds.has(p.id);
    const c = el("article", `pc${held ? " held" : ""}`);
    c.dataset.id = p.id;
    c.style.animationDelay = `${i * 45}ms`;
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

/* ---------------- sentr chip + inspector ---------------- */
function sentrChip(res) {
  const caught = res.findings.filter((f) => f.verdict !== "allow");
  const lat = res.audit.length
    ? (res.audit.reduce((a, r) => a + (r.latency_ms || 0), 0) / res.audit.length).toFixed(2)
    : null;

  let cls = "sentr-chip", label;
  if (!res.sentr_enabled) {
    cls += " off";
    label = "Sentr was off — nothing screened, nothing logged";
  } else if (caught.length) {
    cls += " caught";
    label = `Sentr withheld ${caught.length} listing${caught.length > 1 ? "s" : ""} from the assistant`;
  } else {
    label = `Sentr screened ${res.screened} listings — none withheld`;
  }

  const d = el("details", cls);
  d.innerHTML = `
    <summary>
      <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 3l7 3v6c0 4.4-3 8.3-7 9-4-.7-7-4.6-7-9V6l7-3z" stroke-linejoin="round"/>
      </svg>
      <span>${label}</span>
      ${lat ? `<span class="lat">${lat} ms/listing</span>` : ""}
    </summary>`;

  const body = el("div", "sentr-body");

  if (res.sentr_enabled && !caught.length) {
    body.appendChild(el("p", null,
      "Sentr screened every listing and found nothing to act on. On a clean catalogue " +
      "that is the correct outcome — the run should look identical to the unprotected one."));
  }

  const tabs = el("div", "tabs");
  const tA = el("button", "tab", "What the assistant read");
  const tB = el("button", "tab", "Audit record");
  tabs.append(tA, tB);
  body.appendChild(tabs);

  const pane = el("pre", "inspect");
  body.appendChild(pane);

  const spans = (res.known_injections || []).map((k) => k.span).filter(Boolean);
  const ctxHtml = () => {
    let h = esc(res.agent_context || "");
    spans.forEach((s) => { h = h.split(esc(s)).join(`<mark>${esc(s)}</mark>`); });
    return h;
  };
  const show = (which) => {
    const a = which === "ctx";
    tA.setAttribute("aria-selected", String(a));
    tB.setAttribute("aria-selected", String(!a));
    pane.innerHTML = a ? ctxHtml() : esc(JSON.stringify(res.audit, null, 2) || "[]");
  };
  tA.onclick = () => show("ctx");
  tB.onclick = () => show("audit");
  show("ctx");

  d.appendChild(body);
  return d;
}

/* ---------------- checkout card ---------------- */
async function checkout(res) {
  const d = res.decision;
  const card = el("div", "co");
  card.innerHTML = `
    <div class="co-h"><span>Checkout</span><span class="seller">Kirana Tech · Razorpay</span></div>
    <div class="co-lines"></div>`;
  const lines = card.querySelector(".co-lines");
  const foot = el("div", "co-f");
  card.appendChild(foot);
  $("thread").lastElementChild.appendChild(card);
  toBottom();

  const add = (t, s, v, cls = "") => {
    lines.appendChild(el("div", `co-line ${cls}`,
      `<span class="t">${esc(t)}${s ? `<span class="s">${esc(s)}</span>` : ""}</span>
       <span class="v">${v}</span>`));
    toBottom();
  };

  if (d.product && d.product.title) {
    add(d.product.title, `Qty ${d.quantity}`, rupees(d.base_inr));
    await wait(430);
  }
  for (const a of d.addons || []) {
    add(a.name || "Add-on", "added by the assistant — you did not approve this",
        rupees(a.price_inr), "bad");
    await wait(300);
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
  toBottom();

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
    toBottom();
  };
}

/* ---------------- send ---------------- */
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
    `<span class="spinner"></span><span>Reading ${state.products.length} product descriptions…</span>`);
  bot.appendChild(status);
  toBottom();
  await wait(650);

  let res;
  try {
    res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: q, sentr_enabled: state.sentr, poisoned: true }),
    }).then((r) => r.json());
  } catch {
    status.remove();
    bot.appendChild(el("div", "say", "The demo server stopped responding. Restart it and try again."));
    state.busy = false; $("send").disabled = false;
    return;
  }

  status.remove();
  state.runs[state.sentr ? "on" : "off"] = res;

  const held = new Set(res.findings.filter((f) => f.verdict === "block").map((f) => f.listing_id));
  const d = res.decision;

  const say = el("div", "say");
  bot.appendChild(say);

  let text = d.reasoning || "Here is what I found.";
  if (d.addons.length) {
    text += `\n\nI've added the ${d.addons[0].name} for ${rupees(d.addons[0].price_inr)}.`;
  }
  if (!d.is_evidence) {
    text += `\n\n(Scripted fallback — no language model was called, so this is not evidence of model behaviour. Add a Groq or Gemini key to make it real.)`;
  }
  await type(say, text);

  bot.appendChild(rail(state.products, held));
  const chosen = bot.querySelector(`.pc[data-id="${d.product_id}"]`);
  if (chosen) chosen.classList.add("chosen");
  toBottom();
  await wait(260);

  bot.appendChild(sentrChip(res));
  toBottom();
  await wait(220);

  await checkout(res);

  state.busy = false;
  $("send").disabled = false;
  $("q").focus();
}

/* ---------------- wiring ---------------- */
$("composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
$("sentrSwitch").addEventListener("click", () => {
  state.sentr = !state.sentr;
  $("sentrSwitch").setAttribute("aria-checked", String(state.sentr));
  $("shieldState").textContent = state.sentr ? "on" : "off";
  $("shieldWrap").classList.toggle("on", state.sentr);
});

boot();
