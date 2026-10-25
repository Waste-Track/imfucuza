"""Waste verification classifier: the web layer. All rules live in service.py;
this file only maps HTTP requests to it. Contract: spec/classifier-contract.md.

    GET  /v1/health        200 once the model is loaded (Render health check)
    POST /v1/verify        the Engine's call (JSON with a signed photo link)
    POST /v1/verify-upload test/demo: upload a photo directly (reviewer, admin)
    GET  /metrics          live monitoring hooks and alerts (reviewer, admin)
    GET  /v1/logs          recent request events, no images or links (reviewer, admin)
"""
import os
import pathlib
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from monitoring import Monitor
from security import RateLimiter, parse_tokens
from service import ClassifierService
from validation import MAX_BYTES

STATE = {}


@asynccontextmanager
async def lifespan(app):
    from photo_fetch import make_fetcher
    from verifier import Verifier, fetch_model_files

    tokens = parse_tokens(os.environ.get('SERVICE_TOKENS', ''))
    if not tokens:
        raise RuntimeError('SERVICE_TOKENS is not set - refusing to start without authentication')
    hosts = os.environ.get('PHOTO_URL_ALLOWED_HOSTS', '').split(',')
    if not any(h.strip() for h in hosts):
        raise RuntimeError('PHOTO_URL_ALLOWED_HOSTS is not set - refusing to fetch photos from anywhere')
    w, c = fetch_model_files()
    verifier = Verifier(w, c, os.environ.get('EXPECTED_WEIGHTS_SHA256') or None)
    log_dir = pathlib.Path(os.environ.get('LOG_DIR', 'logs'))
    log_dir.mkdir(parents=True, exist_ok=True)
    monitor = Monitor(log_path=log_dir / 'requests.jsonl', baseline_hist=verifier.cfg.get('baseline_confidence_shares'))
    STATE['svc'] = ClassifierService(verifier, monitor, tokens,
                                     RateLimiter(int(os.environ.get('RATE_LIMIT_PER_MIN', '60'))),
                                     make_fetcher(), hosts)
    yield


app = FastAPI(title='Waste Verification Classifier', version='1.0', lifespan=lifespan,
              docs_url='/docs' if os.environ.get('ENABLE_DOCS', '1') == '1' else None, redoc_url=None)


def reply(result):
    status, body = result
    return JSONResponse(status_code=status, content=body, headers={'Cache-Control': 'no-store'})


@app.exception_handler(RequestValidationError)
async def bad_request(request, exc):
    # FastAPI's default is 422, which the Engine reads as "retake the photo". A malformed
    # request is not a photo problem, so answer 400 instead.
    return reply((400, {'error': 'invalid_request'}))


@app.get('/v1/health')
@app.get('/health')
def health():
    return reply(STATE['svc'].health())


@app.post('/v1/verify')
async def verify(request: Request, authorization: Annotated[str | None, Header()] = None,
                 idempotency_key: Annotated[str | None, Header()] = None):
    raw = await request.body()
    return reply(await run_in_threadpool(STATE['svc'].verify_json, authorization, idempotency_key, raw))


@app.post('/v1/verify-upload')
async def verify_upload(image: Annotated[UploadFile, File()],
                        declared_type: Annotated[str | None, Form()] = None,
                        authorization: Annotated[str | None, Header()] = None):
    data = await image.read(MAX_BYTES + 1)
    return reply(await run_in_threadpool(STATE['svc'].verify_upload, authorization, data, declared_type))


@app.get('/metrics')
def metrics(window_hours: Annotated[float, Query(gt=0, le=24 * 30)] = 24.0,
            authorization: Annotated[str | None, Header()] = None):
    return reply(STATE['svc'].metrics(authorization, window_hours))


@app.get('/v1/logs')
def logs(limit: Annotated[int, Query(ge=1, le=1000)] = 100,
         authorization: Annotated[str | None, Header()] = None):
    return reply(STATE['svc'].logs(authorization, limit))
