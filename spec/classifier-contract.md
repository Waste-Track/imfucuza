# Classifier contract

The HTTP interface between the Engine and the waste classifier (see the [learned component boundary](README.md#learned-component-boundary)). The Engine is the only caller.

## Verify a photo

`POST {CLASSIFIER_URL}/v1/verify`

Headers:

- `Authorization: Bearer {CLASSIFIER_TOKEN}`, a token shared with the Engine. Refuse other callers with 401.
- `Idempotency-Key: {verification_id}`, so you can spot the Engine retrying a request. Classifying a repeat again is fine.

Request body:

```json
{
  "verification_id": "uuid",
  "request_id": "uuid",
  "declared_type": "pet | hdpe | pp | mixed_plastic",
  "photo": {"url": "https://...", "sha256": "hex"},
  "captured_at": "2026-10-11T09:30:00+00:00",
  "capture_source": "rider | household"
}
```

- `declared_type` is what the household says the plastic is. Use `wrong_type` when the photo shows something that isn't a plastic we collect.
- `photo.url` is a signed link, valid for 5 minutes. The photo is a JPEG, at most 1600 px on its longest side, with EXIF removed. `photo.sha256` is the hash of those bytes: if they don't match, reply 422. If you can't fetch the photo at all, reply 503.
- `request_id` (the pickup's id), `captured_at` and `capture_source` are for your logs and monitoring. No name, phone number, location or payment data is sent.

Replies:

- **200**: the decision.

  ```json
  {
    "verification_id": "the id from the request",
    "decision": "accept | reject | uncertain",
    "confidence": 0.93,
    "reason": "ok | contaminated | wrong_type | duplicate",
    "model_version": "cls-2026.11.0"
  }
  ```

  `confidence` runs from 0 to 1 and `model_version` is up to 100 characters. Pair `accept` with `ok` and `reject` with one of the other reasons. With `uncertain`, give the likeliest reason. A mismatched pair goes to the rider.

  Send `duplicate` only when you recognise the photo from an earlier request, because it rejects whatever the decision and confidence. It's optional: the Engine already catches identical and resized copies before calling you.
- **422**: the photo can't be used, for example blurred, too dark or with nothing recognisable in frame. The Engine asks whoever took it for another. After 2 retakes it sends the pickup to a supervisor.
- Any other status means the classifier is unavailable.

## Timeouts and failures

- The Engine waits up to 8 seconds. After a timeout, a connection error, a 5xx or a 429 it retries once, 500 ms later.
- A request that still fails, gets an unavailable status, or gets a reply that breaks the schema above or carries a different `verification_id` sends the pickup to a supervisor, never to an automatic accept or reject.
- After 3 such requests in a row, counting a request and its retry as one, the Engine stops calling for 60 seconds. It then tries again, and one more failure pauses it again. A 422 doesn't count.

## What the Engine does with the decision

The policy belongs to the Engine. The thresholds are starting values until labelled pilot data calibrates them.

| Reply | Outcome |
| --- | --- |
| `reason` is `duplicate` | Reject, whatever the confidence |
| `accept`, `ok`, confidence at least 0.90 | Accept |
| `reject`, a reason other than `ok`, confidence at least 0.80 | Reject |
| Anything else | The rider inspects the plastic and decides |

## Health

`GET {CLASSIFIER_URL}/v1/health` returns 200 once the model is loaded. The Engine doesn't call it, but your host's health check can.

## Trying it against the Engine

The Engine's tests use a fake classifier. To call a real one from a local Engine, set `CLASSIFIER=http`, `CLASSIFIER_URL` and `CLASSIFIER_TOKEN` (see [`../backend/README.md`](../backend/README.md)).
