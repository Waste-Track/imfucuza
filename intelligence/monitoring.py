"""Live monitoring hooks for the verification classifier (signal S1 of the M1 spec,
plus operational signals). Every request becomes an event; /metrics summarises a
time window and raises alerts. Events are also appended to a JSON-lines log.

Never logged: images, photo links (they carry a signing token), or personal data.
"""
import json
import math
import os
import threading
import time
from collections import Counter, deque

ALERTS = {
    # Engine policy (backend/README.md): accept needs >= 0.90, reject >= 0.80; the rest
    # goes to the rider. A high deferred share means the model is rarely sure.
    'deferred_share_max': 0.70,
    # Share of photos we answer 422 (unusable -> retake). Offline, 33% of web images were junk;
    # real pickup photos should be cleaner.
    'unusable_share_max': 0.30,
    # Near-duplicate photos across different verifications: a reuse/fraud signal.
    'duplicate_rate_max': 0.05,
    # The Engine gives up after 8 s; stay well inside that.
    'latency_p95_ms_max': 6000,
    # Population stability index of the confidence distribution vs the offline baseline.
    'confidence_psi_warn': 0.10,
    'confidence_psi_alert': 0.20,
}
BINS = 10
ENGINE_ACCEPT_AT = float(os.environ.get('ENGINE_ACCEPT_AT', '0.90'))
ENGINE_REJECT_AT = float(os.environ.get('ENGINE_REJECT_AT', '0.80'))


def psi(live_counts, baseline_shares, eps=1e-4):
    """PSI = sum over bins of (a - e) * ln(a / e); a = live share, e = baseline share."""
    n = sum(live_counts)
    if n == 0 or not baseline_shares or len(baseline_shares) != len(live_counts):
        return None
    total = 0.0
    for c, e in zip(live_counts, baseline_shares, strict=True):
        a = max(c / n, eps)
        e = max(e, eps)
        total += (a - e) * math.log(a / e)
    return total


def percentile(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def histogram(confs):
    hist = [0] * BINS
    for c in confs:
        hist[min(int(c * BINS), BINS - 1)] += 1
    return hist


def deferred_by_engine(event):
    """Would the Engine's policy send this decision to the rider?"""
    if event['decision'] == 'accept':
        return event['confidence'] < ENGINE_ACCEPT_AT
    if event['decision'] == 'reject':
        return event['confidence'] < ENGINE_REJECT_AT
    return True


class Monitor:
    def __init__(self, log_path=None, baseline_hist=None, max_events=20000, dup_hamming=5):
        self.events = deque(maxlen=max_events)
        self.log_path = log_path
        self.baseline = baseline_hist
        self.started = time.time()
        self.lock = threading.Lock()
        self.seen = deque(maxlen=50000)        # (sha256, dhash, verification_id)
        self.dup_hamming = dup_hamming

    def check_duplicate(self, sha, dh, verification_id):
        """Earlier verification (a different id) with the same or a near-identical photo.
        A retry of the same verification is not a duplicate."""
        with self.lock:
            match = None
            for prev_sha, prev_dh, prev_id in self.seen:
                if prev_id == verification_id:
                    continue
                if prev_sha == sha or (prev_dh ^ dh).bit_count() <= self.dup_hamming:
                    match = prev_id
                    break
            self.seen.append((sha, dh, verification_id))
        return match

    def record(self, event):
        event = dict(event, ts=time.time())
        with self.lock:
            self.events.append(event)
            if self.log_path:
                with open(self.log_path, 'a') as f:
                    f.write(json.dumps(event, default=str) + '\n')

    def recent(self, limit=100):
        with self.lock:
            return list(self.events)[-limit:]

    def metrics(self, window_hours=24.0):
        cutoff = time.time() - window_hours * 3600
        with self.lock:
            ev = [e for e in self.events if e['ts'] >= cutoff]
        ok = [e for e in ev if e.get('outcome') == 'predicted']
        unusable = [e for e in ev if e.get('outcome') == 'unusable_photo']
        answered = len(ok) + len(unusable)
        confs = [e['confidence'] for e in ok]
        hist = histogram(confs)
        per_type = {}
        for t in sorted({e.get('declared_type') or 'unknown' for e in ok}):
            sub = [e['confidence'] for e in ok if (e.get('declared_type') or 'unknown') == t]
            per_type[t] = {'n': len(sub), 'mean': round(sum(sub) / len(sub), 4), 'histogram': histogram(sub)}
        lat = [e['latency_ms'] for e in ok]
        model_lat = [e['model_ms'] for e in ok if 'model_ms' in e]
        p = psi(hist, self.baseline) if ok and self.baseline else None
        m = {
            'window_hours': window_hours,
            'uptime_hours': round((time.time() - self.started) / 3600, 2),
            'requests_total': len(ev),
            'outcomes': dict(Counter(e.get('outcome') for e in ev)),
            'predictions': len(ok),
            'decisions': dict(Counter(f"{e['decision']}/{e['reason']}" for e in ok)),
            'deferred_share_under_engine_policy':
                round(sum(deferred_by_engine(e) for e in ok) / len(ok), 4) if ok else None,
            'unusable_share': round(len(unusable) / answered, 4) if answered else None,
            'unusable_reasons': dict(Counter(e.get('error') for e in unusable)),
            'confidence_mean': round(sum(confs) / len(confs), 4) if confs else None,
            'confidence_histogram': {'bins': [round(i / BINS, 1) for i in range(BINS + 1)], 'counts': hist},
            'confidence_by_declared_type': per_type,
            'confidence_psi_vs_offline': round(p, 4) if p is not None else None,
            'duplicate_rate': round(sum(bool(e.get('duplicate_of')) for e in ok) / len(ok), 4) if ok else None,
            'latency_ms_p50': round(percentile(lat, 0.5), 1) if lat else None,
            'latency_ms_p95': round(percentile(lat, 0.95), 1) if lat else None,
            'model_ms_p95': round(percentile(model_lat, 0.95), 1) if model_lat else None,
        }
        m['alerts'] = self.alerts(m)
        m['thresholds'] = dict(ALERTS, engine_accept_at=ENGINE_ACCEPT_AT, engine_reject_at=ENGINE_REJECT_AT)
        return m

    @staticmethod
    def alerts(m):
        out = []

        def over(key, limit, label):
            if m.get(key) is not None and m[key] > limit:
                out.append(f'{label}: {m[key]} > {limit}')

        over('deferred_share_under_engine_policy', ALERTS['deferred_share_max'], 'most decisions deferred to riders')
        over('unusable_share', ALERTS['unusable_share_max'], 'many unusable photos')
        over('duplicate_rate', ALERTS['duplicate_rate_max'], 'duplicate photos high')
        over('latency_ms_p95', ALERTS['latency_p95_ms_max'], 'latency p95 near the Engine timeout')
        p = m.get('confidence_psi_vs_offline')
        if p is not None and p > ALERTS['confidence_psi_alert']:
            out.append(f'confidence drift ALERT: PSI {p} > {ALERTS["confidence_psi_alert"]}')
        elif p is not None and p > ALERTS['confidence_psi_warn']:
            out.append(f'confidence drift warning: PSI {p} > {ALERTS["confidence_psi_warn"]}')
        blocked = m['outcomes'].get('url_blocked', 0)
        if blocked:
            out.append(f'security: {blocked} request(s) asked for a photo link outside our storage')
        if m['outcomes'].get('auth_failed', 0) >= 10:
            out.append(f"security: {m['outcomes']['auth_failed']} failed authentications")
        if m['predictions'] < 30:
            out.append(f"note: only {m['predictions']} predictions in window; rates are not yet reliable")
        return out
