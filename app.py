"""Noor Guide server. A lean fork of orbit.ai's app.py: no auth, billing or
Firestore, because it runs on the operator's own device."""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import languages
import storage
from config import TOUR_TYPES, get_settings
from engines.llm import build_llm
from engines.stt import WhisperSTT
from engines.translate import NllbTranslator, PassthroughTranslator
from insights import build_business_insights, translate_reviews
from session_manager import Engines, TourHub

STATIC_DIR = Path(__file__).resolve().parent / "static"


def build_engines() -> Engines:
    settings = get_settings()
    try:
        import ctranslate2  # noqa: F401
        import sentencepiece  # noqa: F401

        translator: Any = NllbTranslator(settings.nllb_model)
    except ImportError:
        translator = PassthroughTranslator()
    return Engines(
        stt=WhisperSTT(settings.stt_model, settings.stt_device, settings.stt_compute_type),
        translator=translator,
        llm=build_llm(settings),
    )


async def warm_up(engines: Engines) -> None:
    """Load models in the background so the first visitor question is not the
    one that waits for a model to load."""

    engines.llm_available = await engines.llm.available()
    for name in ("stt", "translator"):
        engine = getattr(engines, name)
        load = getattr(engine, "load", None)
        if load is None:
            continue
        with contextlib.suppress(Exception):
            await asyncio.to_thread(load)
    if hasattr(engines.translator, "translate") and getattr(engines.translator, "ready", False):
        with contextlib.suppress(Exception):
            await asyncio.to_thread(engines.translator.translate, "Welcome to the farm.", "en", "fr")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engines = build_engines()
    app.state.hub = TourHub(settings, engines)
    app.state.warmup = None
    if settings.preload_models:
        app.state.warmup = asyncio.create_task(warm_up(engines))
    else:
        engines.llm_available = await engines.llm.available()
    yield
    if app.state.warmup is not None:
        app.state.warmup.cancel()


app = FastAPI(title="Noor Guide", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _hub(request: Request | WebSocket) -> TourHub:
    return request.app.state.hub


# ---------- Pages ----------

@app.get("/")
async def guide_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "guide.html")


@app.get("/guest")
async def guest_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "guest.html")


@app.get("/insights")
async def insights_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "insights.html")


# ---------- API ----------

@app.get("/api/health")
async def health(request: Request) -> JSONResponse:
    hub = _hub(request)
    engines = hub.engines
    engines.llm_available = await engines.llm.available()
    return JSONResponse(
        {
            "stt": {"ready": engines.stt.ready, "model": get_settings().stt_model, "error": getattr(engines.stt, "error", None)},
            "translation": {
                "ready": engines.translator.ready,
                "model": get_settings().nllb_model,
                "error": getattr(engines.translator, "error", None),
            },
            "llm": {"ready": engines.llm_available, "name": engines.llm.name},
            "tour_running": bool(hub.session and hub.session.running),
        }
    )


@app.get("/api/meta")
async def meta() -> JSONResponse:
    return JSONResponse({"languages": languages.to_json(), "tour_types": TOUR_TYPES})


@app.get("/api/profile")
async def get_profile() -> JSONResponse:
    return JSONResponse(storage.get_profile())


@app.post("/api/profile")
async def save_profile(profile: dict[str, Any] = Body(...)) -> JSONResponse:
    if not isinstance(profile.get("stops"), list):
        raise HTTPException(400, "A profile needs a list of stops.")
    return JSONResponse(storage.save_profile(profile))


@app.get("/api/tours")
async def tours() -> JSONResponse:
    return JSONResponse(storage.list_tours())


@app.get("/api/tours/{tour_id}")
async def tour(tour_id: str) -> JSONResponse:
    data = storage.get_tour(tour_id)
    if data is None:
        raise HTTPException(404, "Tour not found.")
    return JSONResponse(data)


@app.get("/api/reviews")
async def reviews() -> JSONResponse:
    return JSONResponse(storage.list_reviews())


@app.post("/api/reviews")
async def add_reviews(request: Request, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    if payload.get("sample"):
        items = storage.sample_reviews()
    elif isinstance(payload.get("items"), list):
        items = payload["items"]
    else:
        items = storage.parse_reviews(str(payload.get("raw") or ""))
    if not items:
        raise HTTPException(400, "No reviews found in what was sent.")
    all_reviews = storage.add_reviews(items)
    if await translate_reviews(all_reviews, _hub(request).engines.translator):
        storage.update_reviews(all_reviews)
    return JSONResponse(all_reviews)


@app.delete("/api/reviews")
async def clear_reviews() -> JSONResponse:
    storage.clear_reviews()
    return JSONResponse([])


@app.get("/api/insights")
async def last_insights() -> JSONResponse:
    return JSONResponse(storage.get_insights() or {})


@app.post("/api/insights")
async def make_insights(request: Request) -> JSONResponse:
    engines = _hub(request).engines
    engines.llm_available = await engines.llm.available()
    all_reviews = storage.list_reviews()
    if await translate_reviews(all_reviews, engines.translator):
        storage.update_reviews(all_reviews)
    data = await build_business_insights(
        storage.all_tours_full(), all_reviews, storage.get_profile(), engines.llm, engines.llm_available
    )
    data["model"] = engines.llm.name if data.get("source") == "llm" else "rules"
    storage.save_insights(data)
    return JSONResponse(data)


@app.post("/api/translate")
async def translate(request: Request, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    engines = _hub(request).engines
    text = str(payload.get("text") or "")[:1000]
    out = await asyncio.to_thread(engines.translator.translate, text, payload.get("src"), payload.get("tgt"))
    return JSONResponse({"text": out, "translated": engines.translator.ready})


@app.post("/api/translate_batch")
async def translate_batch(request: Request, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Labels for the guest screen's question tiles, in the guest's language."""

    engines = _hub(request).engines
    texts = [str(t)[:300] for t in (payload.get("texts") or [])][:40]
    src, tgt = payload.get("src") or "en", payload.get("tgt")

    def run() -> list[str]:
        return [engines.translator.translate(t, src, tgt) for t in texts]

    try:
        out = await asyncio.to_thread(run)
    except Exception:
        out = texts
    return JSONResponse({"texts": out, "translated": engines.translator.ready})


# ---------- WebSocket ----------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    hub = _hub(websocket)
    role = websocket.query_params.get("role", "guide")
    client = hub.add(websocket, role)
    await hub.send(websocket, "tour_state", **hub.state())
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                if hub.session is not None and hub.session.running:
                    await hub.session.handle_audio(client, message["bytes"])
            elif message.get("text") is not None:
                await _handle_text(hub, client, message["text"])
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:
        if "disconnect" not in str(exc).lower() and "close" not in str(exc).lower():
            raise
    finally:
        hub.remove(websocket)


async def _handle_text(hub: TourHub, client: Any, raw: str) -> None:
    try:
        message: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        await hub.send(client.websocket, "error", message="Invalid JSON.")
        return
    kind = message.get("type")
    if kind == "start":
        if client.role != "guide":
            return
        await hub.start(message)
    elif kind == "stop":
        if client.role == "guide" and hub.session is not None:
            await hub.session.stop()
    elif hub.session is not None and hub.session.running:
        # Guests may only speak, blink, type, or choose their own screen's language.
        if client.role == "guest" and kind not in {"guest_text", "guest_language"}:
            return
        await hub.session.handle_message(client, message)
    else:
        await hub.send(client.websocket, "status", level="info", message="No tour is running yet.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000)
