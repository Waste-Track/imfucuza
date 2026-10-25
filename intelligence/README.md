# Intelligence

The waste verification classifier, the system's one learned component. The Engine calls it over HTTP
as set out in [`../spec/classifier-contract.md`](../spec/classifier-contract.md). It receives a photo link and
request metadata, and returns a decision and a confidence. It never sees identity, location or payment data, and
it never moves money: the Engine's policy decides.

## The model (v1)

| | |
|---|---|
| Architecture | ViT-S/16 backbone, chosen over MobileNetV3, EfficientNet-B0, ResNet-50 and ConvNeXt-Tiny (Stage 1 notebooks), with two heads: **material** (plastic / non_plastic / discard) and **cleanliness** (clean / contaminated) |
| Training data | RealWaste (4,752 images, CC BY 4.0) plus human-labelled web images from Ghana (Stage 2 notebooks). Research use only; images are not in this repo |
| Served as | ONNX (exported from PyTorch; the deploy notebook checks the outputs match), so the service needs no PyTorch and fits a 512 MB free instance |
| Registry | Private Hugging Face model repo. The service downloads it at start-up, **pinned to a commit** and **checked by SHA-256** |
| Local test results | Pay/no-pay decision 91.2% correct; clean plastic recall 100% (14/14); exact 4-way decision 60.2% (n = 113) |
| Intended use | Assistant to a human verifier during the pilot. Not for unsupervised payouts |

How the model output becomes a contract reply:

| Model says | Reply |
|---|---|
| plastic + clean | `200` `accept` / `ok` |
| plastic + contaminated | `200` `reject` / `contaminated` |
| non-plastic | `200` `reject` / `wrong_type` |
| discard (no recognisable waste) | `422`: the Engine asks for a retake |

`confidence` is temperature-calibrated. The Engine applies its own thresholds (accept at 0.90 or above, reject at 0.80 or above).
`duplicate` is never sent. The Engine already rejects identical and resized photos, and a perceptual hash would risk
rejecting honest households whose sacks look alike. Reuse is still **measured** here (`duplicate_rate` in `/metrics`).

## Endpoints

| Method | Path | Caller | |
|---|---|---|---|
| GET | `/v1/health` | anyone | 200 once the model is loaded (Render health check) |
| POST | `/v1/verify` | Engine (`backend` token) | The contract call |
| POST | `/v1/verify-upload` | `reviewer`, `admin` | Test and demo: upload a photo directly (multipart field `image`, optional `declared_type`) |
| GET | `/metrics?window_hours=24` | `reviewer`, `admin` | Monitoring hooks and alerts |
| GET | `/v1/logs?limit=100` | `reviewer`, `admin` | Recent events (never images, photo links or personal data) |

Status codes: `200` decision · `422` **only** for an unusable photo (unreadable, too small, hash mismatch, nothing
recognisable) · `400` malformed request, unknown field, or a photo link outside our storage · `401`/`403` auth ·
`429` rate limit · `503` photo couldn't be fetched · `500` internal (the response includes a `ref` that matches the server log).

## Security controls

- **Tokens per caller** (`backend`, `reviewer1-3`, `admin`), compared in constant time; roles limit what each token can do.
- **Photo links are allow-listed:** https only, our Supabase host only, `/storage/v1/object/sign/` paths only, no
  credentials or odd ports, redirects not followed, downloads capped at 8 MB with a 4 s timeout. This blocks server-side request forgery.
- **The boundary is enforced:** requests with any field outside the contract (for example a phone number) are refused with `400`.
- **Images are checked** by their actual bytes (format, size, pixel count against decompression bombs, EXIF rotation),
  then re-encoded, which drops all metadata, including GPS.
- **Model integrity:** the service refuses to start if the downloaded weights don't match `EXPECTED_WEIGHTS_SHA256`.
- **Idempotency:** an Engine retry (same `Idempotency-Key`) gets the cached answer; the key must equal `verification_id`.
- **Rate limit:** 60 requests per minute per token. Non-root container. No secrets in this repo.

## Monitoring (M1 signal S1 and operational hooks)

`/metrics` reports, per time window:
- the confidence histogram, overall and **per declared waste type** (S1), with a **PSI drift score** against the offline test baseline,
- the decision mix and the share the Engine policy would defer to riders,
- the unusable-photo share and its reasons,
- duplicate rate, and p50/p95 latency (total and model-only),
- security events (blocked links, failed authentications),
- alerts whenever a threshold is crossed (thresholds are in `monitoring.py`, each with its reason).

## Configuration (Render environment variables; never commit these)

| Variable | Purpose |
|---|---|
| `SERVICE_TOKENS` | `name:role:token;...` (roles: `backend`, `reviewer`, `admin`); the service won't start without it |
| `PHOTO_URL_ALLOWED_HOSTS` | Supabase project host, for example `abcd.supabase.co`; the service won't start without it |
| `MODEL_REPO`, `MODEL_REVISION` | Private Hugging Face model repo and the commit to serve |
| `HF_TOKEN` | **Read-only** Hugging Face token for the private model |
| `EXPECTED_WEIGHTS_SHA256` | SHA-256 of `verifier_v1.onnx` |
| `ORT_THREADS`, `RATE_LIMIT_PER_MIN`, `ENABLE_DOCS` | Optional (1, 60, 1) |

## Layout

| Path | What |
|---|---|
| `main.py` | Web layer only (FastAPI) |
| `service.py` | Every contract rule: auth, schema, idempotency, link allow-list, fetch, hash check, decision mapping |
| `verifier.py`, `preprocess.py`, `decision.py` | ONNX model, preprocessing identical to training, decision logic |
| `validation.py`, `security.py`, `monitoring.py`, `photo_fetch.py` | Input checks, tokens and rate limit, metrics, safe photo download |
| `tests/` | Contract, logic and HTTP tests (no model or network needed) |
| `notebooks/` | The research record: model selection, data collection, labelling, fine-tuning, deployment |

## What CI expects

- `requirements.txt` in this folder (installed with `uv pip`).
- Tests under `tests/`, run with `pytest`. Code must pass `ruff check` (rules pinned in `ruff.toml`).
- No model weights or datasets in Git. They live in the private model repo and are downloaded at start-up.

Run locally: `pip install -r requirements.txt pytest && pytest && ruff check .`
