"""Decision logic for the waste verification classifier (v1).

Pure numpy, no torch, so it can be unit-tested anywhere. It must stay identical
to the logic used when the model was evaluated (Stage 2c notebook, cell 6).
"""
import numpy as np

DECISIONS = ['accept', 'reject_contaminated', 'reject_wrong_type', 'retake_photo']
UNCERTAIN = 'uncertain_human_review'


def apply_temperature(p, t):
    """Rescale probabilities by temperature t (t < 1 sharpens, t > 1 softens)."""
    z = np.log(np.clip(p, 1e-12, 1)) / t
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def decide(pm, pc):
    """Combine the material head (plastic, non_plastic, discard) and the cleanliness
    head (clean, contaminated) into one decision index and a decision confidence."""
    mat = pm.argmax(1)
    cl = pc.argmax(1)
    dec = np.select([mat == 2, mat == 1, (mat == 0) & (cl == 1)], [3, 2, 1], default=0)
    conf = np.where(mat == 0, pm.max(1) * pc.max(1), pm.max(1))
    return dec, conf


def final_decision(pm, pc, threshold):
    """Single-image helper: returns (decision, suggested_decision, confidence, status)."""
    dec, conf = decide(pm, pc)
    suggested = DECISIONS[int(dec[0])]
    c = float(conf[0])
    if c >= threshold:
        return suggested, suggested, c, 'auto'
    return UNCERTAIN, suggested, c, 'review'
