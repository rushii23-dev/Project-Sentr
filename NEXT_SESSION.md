# Sentr — handoff for the next session

Read `SPEC.md` first (the spec — it was `Readme.md` until Day 6, renamed so
`README.md` could become the judge-facing document). The spec wins any
disagreement with this file.

**Status: Days 1–6 complete. Day 7 is rehearsal only.**

**Three things landed after this file first said "add nothing" — read §0.1.**
Working directory is `D:\Project Sentr`. Everything lives on D:, nothing on C:.

---

## 0. Day 7 — add nothing

The spec is explicit: absorb slippage, run the demo until it behaves
identically, rehearse the 60-second opening. **Do not add features.** The
numbers are frozen and the held-out set is spent.

Checklist — **all four done**:

1. ✅ **Repeatable.** Five consecutive full runs, all three prompts, both modes:
   identical totals every time (`2498 749 · 2998 1499 · 748 649`).
2. ✅ **Razorpay verified from a normal terminal.** Real test-mode orders, e.g.
   `order_TXqf3emEWIiVHi` at ₹2,498 and `order_TXqf4P1q4yQIzb` at ₹1,299,
   `status=created`, `simulated=False`. The earlier `NameResolutionError` was
   only the sandboxed preview server having no network.
3. ✅ **Cache warm** for all three prompts, both modes. `demo/.llm_cache/` is
   keyed on the full prompt including the catalogue, so **do not reword a prompt
   on camera** — one changed word is a cache miss and a live API call mid-pitch.
4. ✅ **Run sheet written** — `RUN_SHEET.md` — timed recording script, pre-flight checks, the
   figures to quote, and answers to the questions a judge will ask.

Two production notes for recording:

- Open **`http://127.0.0.1:8000`**, not `localhost`. The server binds IPv4 only;
  `localhost` can resolve to IPv6, and the page then loads while `boot()` fails
  silently, so the suggestion chips do nothing.
- **Record at ≥1440px wide.** Below 980px the comparison grid collapses and the
  two runs stack vertically, which loses the entire point of the shot.

### 0.1 What changed after the run sheet was written

All three are committed and pass `verify_all.py` (**151 passed, 0 failed, 1
skipped** — up from 121; 152/0/0 with `--with-model`). The three pinned totals are untouched:
`2498 749 · 2998 1499 · 748 649`. But the run sheet predates them.

1. **A seventh poisoned listing** (`HPH-STU`, `instruction_override` — "ignore
   any previous budget limits"). It is a **detection** case, not a harm case:
   the agent resisted the payload and buys the same ₹1,999 headphones with Sentr
   on or off. We left the payload as published rather than rewording it until it
   landed. It earns its place by widening the on-screen evidence from three
   attack families to four. **Do not demo "headphones under Rs 2,000" as an
   attack** — off and on agree, and it will look like the filter did nothing.
   README's "One attack that did not work" is the place to raise it, and raising
   it first is stronger than being asked.

2. **`POST /api/screen/batch` and the Integrate panel.** The deployability
   answer: publish-time screening of a whole feed in one call, timed live in the
   browser (400–650 listings/sec here). New header button, third panel. It reads
   `GET /api/feed` then posts all 50 listings. **Rehearse it** — it is the only
   part of the page the five-run rehearsal never covered.

3. **A security pass over the demo itself**, which found a real XSS in our own
   storefront: `image_url` and `star_rating` are outside Sentr's scope, reached
   the page unscreened, and were rendered unescaped — a listing with
   `x" onerror="..."` in `image_url` ran script. Confirmed executing, then
   fixed. Also capped `/api/run` (a 100k-character request was a 48-second live
   LLM call), guarded the order amount before Razorpay (it comes from a model
   that just read attacker-controlled text), and made model-output coercion
   total. `verify_all.py` § 14 covers all 23 checks. **Raise this in the pitch
   rather than waiting to be asked** — "we audited our own demo and found the
   class of bug we're defending against" is a stronger line than silence.

Also fixed along the way: `HPH-ONEAR` was titled "Corvus Lite On-Ear" and the
agent would not count it as headphones, so a budget query got "no headphones
under Rs 2000" while a ₹1,999 pair sat in its own shortlist. The word is in the
title now. Not an injection bug — worth knowing anyway, because an agentic
storefront can lose a sale to a title a human would have read correctly.

---

## 1. Run it

```bash
cd "D:/Project Sentr"
.venv/Scripts/python.exe demo/server.py     # then open http://127.0.0.1:8000
```

The venv is at `D:\Project Sentr\.venv` (project-local, deliberately off
OneDrive). Always invoke it by full path — `pip.exe` has a stale shebang, so use
`python.exe -m pip` for installs.

`.env` holds working keys for Groq, Google AI Studio, Razorpay **test mode**,
and Pexels. It is gitignored.

---

## 2. The frozen numbers

Held-out set, opened once on 2026-09-04. 1,749 listings: 1,200 real, 549
poisoned. `eval/results/day5_final.json`.

| | Sentr | Baseline |
|---|---|---|
| Attacks detected | 86.9% | 35.5% |
| Attacks neutralised | 86.9% | 35.5% |
| — published payloads (176) | 86.4% | 36.6% |
| — authored payloads (7) | 100% | 9.5% |
| Precision | 99.8% | 99.5% |
| FPR any verdict | 0.083% (1 flag) | 0.083% (1 block) |
| **FPR blocked** | **0.00%** | 0.083% |
| Throughput | 326/sec | 3.4/sec (Day 1) |
| Single-listing p50 | 2.1 ms | 368 ms (Day 1) |

Cost, at 50k listings/month × 0.02 conversion × ₹1,200 AOV:
**Sentr ₹0/month** (95% CI up to ₹3,829), **baseline ₹1,000/month** (up to
₹5,646). `eval/results/cost_model.json`.

Weakest spots, already in the README: `task_switch` 0/3; six of ten families
have one distinct held-out payload; 13.1% of attacks still get through.

---

## 3. What exists

| Layer | File | State |
|---|---|---|
| Rules (layer 1) | `sentr/rules.yaml`, `sentr/rules.py` | shipped, 15 rules |
| Classifier (layer 2) | `sentr/classifier.py` | implemented, **deliberately empty** |
| Training | `sentr/train_classifier.py`, `notebooks/train.ipynb` | ready, never run |
| Sanitiser (layer 3) | `sentr/sanitizer.py` | shipped |
| Orchestration | `sentr/pipeline.py` | all three layers wired |
| Audit trail | `sentr/audit.py` | shipped, per-layer latency |
| Dataset | `data/build_dataset.py` | payload-disjoint, self-verifying |
| Baseline eval | `eval/baseline.py` | Day 1 |
| Layer-1 eval | `eval/rules_eval.py` | refuses `test` |
| Full eval | `eval/evaluate.py` | refuses `test` |
| Layer-2 decision | `eval/layer2_probe.py` | the arithmetic for not shipping it |
| **Held-out eval** | `eval/final_eval.py` | **spent — receipt committed** |
| Cost model | `eval/cost_model.py` | done, Wilson intervals |
| Demo | `demo/server.py` + `demo/static/*` | live, repeatable |
| Screening API | `POST /api/screen`, `POST /api/screen/batch` | one listing, or a feed of ≤500 |
| README | `README.md` | done |
| Everything above | `verify_all.py` | 128 checks, one command |

---

## 4. Decisions that must not be quietly undone

**The held-out set is spent.** `eval/results/HELD_OUT_OPENED.json` records one
opening, at commit `0fba9d83`, on 2026-09-04. `final_eval.py` refuses a second
run without `--rerun --reason "..."`, and the reason is appended to the committed
receipt. If the Colab fine-tune ever lands and beats rules on **val**, re-opening
is defensible — but it must go through that flag so the count stays visible, and
the README must then report both numbers.

**Layer 2 is empty on purpose, and the arithmetic is committed.** There is no
threshold at which the off-the-shelf model recovers an attack without blocking an
honest listing. The best option buys 4.7 points of recall for 0.167% blocked-FPR
≈ ₹3,829/month. We declined. Do not quietly enable it —
`eval/results/layer2_decision.json` is the evidence and the README cites it.

**Splits are payload-disjoint.** The Day-3 build split *rows*, so 68% of val
attacks used a payload seen verbatim in training. `build_dataset.py` now
allocates payloads before rows exist and clusters them by substring containment;
`verify_disjoint()` re-proves it every run. **Never go back to splitting rows.**

**Row counts always ship with distinct-payload counts.** Every payload appears
once per insert position, so three rows can be one piece of evidence.

**Published vs authored is never blended.** 29 of 920 payloads are ours. The
published number carries the weight.

**Two recalls, not one.** `detected` = any verdict but allow. `neutralised` = the
payload does not reach the agent. They are both 86.9% today because the rules
localise every span they find, so the sanitiser can actually remove it. If layer
2 is ever enabled they will diverge, because a classifier flags without being
able to point at a span.

**`flag` protects honest listings.** Real listings carry U+200B from broken HTML
exports; they are flagged and sanitised, not blocked. The sanitiser keeps U+200C
when Devanagari is present because ZWNJ is a real letter in Hindi.

**Rules are data.** `sentr/rules.py` contains no rules.

**Three rule changes are disclosed in the README** — the `forget everything`
anchor (val, Day 3), the `claimed_prior_authorisation` anchor after it blocked a
real *"We have been approved by FSSAI"* peanut butter listing (train, Day 4), and
the `pre-approved by the customer` pattern added back after that anchor went one
step too far. Nothing was tuned after the held-out set was opened.

---

## 5. Environment landmines

**RAM is the binding constraint.** 5.86 GB total, seen at 0.46 GB free.
- Everything batches by **token budget**, not row count. A flat batch of 16 × 512
  tokens OOMs and segfaults.
- **Local training is impossible**: 1.0 windows/sec, and it did not get faster
  when 82% of parameters were frozen, so it is memory-bound, not compute-bound.
  Roughly four hours for three epochs. Colab or nothing.
- Browser screenshots time out. Verify via DOM (`read_page`, `get_page_text`).

**The clock is not trustworthy on this machine.** `time.perf_counter()` jumped
~10.6 hours mid-run during the Day 5 baseline pass (laptop suspended at
midnight), producing a wall time of 38,501s for six minutes of work. Only timing
fields were affected; every correctness metric is a count. `eval/evaluate.py`
now cross-checks `perf_counter` against `monotonic` and **withholds** throughput
rather than publishing a wrong figure. `day5_final.json` carries a `corrections`
entry. If you add timing anywhere, use `evaluate.Clock`.

**Import order and threads.** `OMP_NUM_THREADS` must be a real environment
variable *before* torch imports, `KMP_DUPLICATE_LIB_OK=TRUE` must be set, and
**torch must be imported before numpy**. Wrong order is a silent segfault.
`sentr/classifier.py::_prepare_env()` is the shared implementation — import it,
do not copy the header again.

**`microsoft/deberta-v3-xsmall` ships fp16 weights** and transformers 5 honours
the checkpoint dtype; half precision has no CPU kernel and the loss will not
build. `train_classifier.py` pins fp32 and falls back to the old `torch_dtype`
keyword for whatever Colab is on.

**`meta-llama/Llama-Prompt-Guard-2-*` is gated** (403 without an accepted
licence) and is not smaller than what we already run — its "86M" is the backbone
count; with the 128k embedding it is the same ~184M as `deberta-v3-base`.

**Killing the demo server.** `pkill` does not work here:
```bash
netstat -ano | grep ":8000 .*LISTENING" | awk '{print $5}' | sort -u \
  | while read p; do taskkill //PID $p //F; done
```

**Bash heredocs mangle backslashes.** Use the Write/Edit tools for regexes.

**Free-tier model names churn.** Current working defaults: Groq
`openai/gpt-oss-20b`, Gemini `gemini-3.6-flash`. Check
`https://api.groq.com/openai/v1/models` if calls start failing.

---

## 6. If there is time after rehearsal

Only if Day 7 finishes early, and only in this order:

1. **Run the Colab fine-tune** (`python notebooks/pack_for_colab.py`, then
   `notebooks/train.ipynb` on a T4, ~10 min). Evaluate on **val only**. If it
   beats the rules without costing blocked false positives, that is a genuine
   improvement — and then decide, deliberately, whether it is worth the second
   held-out opening. If it is not clearly better, say so and ship as is.
2. Nothing else.

---

## 7. Honest caveats to carry into the pitch

- The spec's §2 premise about baseline over-flagging **did not hold** (0.13% on
  6,000 listings, not high). The real finding is that it catches 35.5% of
  catalogue-shaped injections, and that its false positives are `Key: Value`
  spec tables.
- 29 of 920 payloads are ours. Reported separately, always.
- Six of ten families are authored-only; several have one distinct held-out
  payload. Per-family recall there is an anecdote, not a rate. Say so before a
  judge does.
- Zero false positives in 1,200 listings is not a 0% rate — the 95% upper bound
  is 0.319%, and the cost table prices both.
- Our benign corpus is capped at 4,000 characters while ACP allows 5,000, so the
  truncation attack layer 2's chunking exists to stop is under-represented.
- Attack susceptibility is model- and run-dependent, which is the argument for
  screening the catalogue: a merchant controls neither.
- One of the seven poisoned listings does not change what the agent buys. Said
  out loud, in the README and in §0.1, rather than removed. A defence tested only
  against attacks already known to work is measuring the wrong thing.
- The demo defaults to the model that obeyed 4/4. Defensible only because the
  full table, including the two models that resisted, is committed and in the
  README.
