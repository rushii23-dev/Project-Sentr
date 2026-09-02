"""FastAPI backend for the Sentr storefront demo.

Serves one page and one endpoint. The page is hand-written HTML/CSS/JS with no
build step (CLAUDE.md section 8) and every asset -- fonts, photos -- is stored
locally, so a demo never depends on a network that might not be there.

Run:  python demo/server.py     then open http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo import agent as agent_mod  # noqa: E402
from demo import checkout as checkout_mod  # noqa: E402
from sentr import audit, pipeline  # noqa: E402

STATIC = ROOT / "demo" / "static"
RESULTS = ROOT / "eval" / "results"

app = FastAPI(title="Sentr demo")


@app.middleware("http")
async def no_cache(request, call_next):
    """Serve assets uncached. This is a demo we edit constantly, and a stale
    app.js in a browser cache is a confusing way to lose ten minutes."""
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


app.mount("/static", StaticFiles(directory=STATIC), name="static")


class RunRequest(BaseModel):
    request: str
    sentr_enabled: bool = True
    poisoned: bool = True
    use_cache: bool = True


def load_catalog(poisoned: bool) -> dict:
    name = "catalog_poisoned.json" if poisoned else "catalog_clean.json"
    return json.loads((ROOT / "demo" / name).read_text(encoding="utf-8"))


def view_of(item: dict) -> dict:
    """Turn one ACP feed item into what the storefront page needs.

    The feed is the authentic artefact -- spec field names, spec types. The
    page wants display-shaped values, so the translation lives here rather
    than polluting either side."""
    payable = agent_mod.money(item)
    listed = float(str(item.get("price", "0")).split()[0] or 0)
    category = (item.get("product_category") or "").split(">")[-1].strip()
    return {
        "id": item.get("item_id", ""),
        "title": item.get("title", ""),
        "subtitle": item.get("pricing_trend") or category,
        "price_inr": payable,
        "mrp_inr": listed if listed > payable else None,
        "image": item.get("image_url", ""),
        "rating": item.get("star_rating", ""),
        "reviews": item.get("review_count", 0),
        "brand": item.get("brand", ""),
        "availability": item.get("availability", ""),
    }


@app.get("/")
def index() -> HTMLResponse:
    """Serve the page with cache-busted asset URLs.

    Stamping each asset with its own mtime means an edited stylesheet or script
    always reaches the browser, without anyone remembering to hard-refresh
    halfway through a demo."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for asset in ("style.css", "app.js"):
        stamp = int((STATIC / asset).stat().st_mtime)
        html = html.replace(f"/static/{asset}", f"/static/{asset}?v={stamp}")
    return HTMLResponse(html)


@app.get("/api/catalog")
def catalog(poisoned: bool = True) -> JSONResponse:
    cat = load_catalog(poisoned)
    return JSONResponse(
        {
            "store": cat.get("seller_name", "Store"),
            "products": [view_of(i) for i in cat["feed"]],
        }
    )


@app.get("/api/status")
def status() -> JSONResponse:
    credits_file = STATIC / "img" / "credits.json"
    credits = json.loads(credits_file.read_text(encoding="utf-8")) if credits_file.exists() else []
    return JSONResponse(
        {
            "providers": agent_mod.providers_available(),
            "razorpay": checkout_mod.keys_available(),
            "photo_credits": credits,
        }
    )


@app.post("/api/run")
def run(req: RunRequest) -> JSONResponse:
    cat = load_catalog(req.poisoned)
    products = cat["feed"]

    tag = "on" if req.sentr_enabled else "off"
    log_path = RESULTS / f"audit_run_{tag}.jsonl"
    audit.clear(log_path)

    screened = pipeline.screen_catalog(
        products, enabled=req.sentr_enabled, log_path=str(log_path)
    )
    visible = pipeline.catalog_for_agent(screened)
    decision = agent_mod.decide(visible, req.request, use_cache=req.use_cache)

    chosen = next((p for p in products if p["item_id"] == decision.product_id), None)
    base = agent_mod.money(chosen) * decision.quantity if chosen else 0.0
    order = checkout_mod.create_order(decision.total_inr, note=f"sentr={tag}")

    # Ground truth from our own fixture, used only to highlight the payload in
    # the inspector. It is never given to the detector or the agent.
    known_injections = [
        {"item_id": i.get("item_id"), "field": i.get("_injected_field"),
         "span": i.get("_injected_span")}
        for i in products if i.get("_poisoned")
    ]

    findings = [
        {
            "listing_id": s.record.listing_id if s.record else "",
            "verdict": s.verdict,
            "confidence": s.confidence,
            "decided_by": s.decided_by,
            "triggers": [t.__dict__ for t in s.triggers],
        }
        for s in screened
        if s.verdict != "allow"
    ]

    return JSONResponse(
        {
            "sentr_enabled": req.sentr_enabled,
            "screened": len(screened),
            "blocked": sum(1 for s in screened if s.verdict == "block"),
            "flagged": sum(1 for s in screened if s.verdict == "flag"),
            "reached_agent": len(visible),
            "findings": findings,
            "decision": {
                "product_id": decision.product_id,
                "product": view_of(chosen) if chosen else {},
                "quantity": decision.quantity,
                "addons": decision.addons,
                "base_inr": base,
                "addon_total_inr": decision.addon_total,
                "total_inr": decision.total_inr,
                "reasoning": decision.reasoning,
                "provider": decision.provider,
                "model": decision.model,
                "cached": decision.cached,
                "is_evidence": decision.is_evidence,
                "errors": decision.errors,
            },
            "order": {
                "order_id": order.order_id,
                "amount_inr": order.amount_inr,
                "status": order.status,
                "simulated": order.simulated,
                "error": order.error,
            },
            "agent_context": decision.prompt,
            "known_injections": known_injections,
            "audit": audit.read_all(log_path),
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
