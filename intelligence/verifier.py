"""Loads the v1 verification model (ONNX) and runs one prediction.

The model was trained in PyTorch (Stage 2c) and exported to ONNX so that the
service runs without PyTorch and fits in a small (512 MB) server. The deploy
notebook checks that the ONNX model and this preprocessing reproduce the
offline results before anything is published.
"""
import hashlib
import json
import os

import numpy as np
import onnxruntime as ort

from decision import apply_temperature, final_decision
from preprocess import to_input

WEIGHTS_FILE = 'verifier_v1.onnx'
CONFIG_FILE = 'verifier_v1_config.json'


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def fetch_model_files():
    """Uses local files if MODEL_DIR is set; otherwise downloads from the private
    Hugging Face model repo named in MODEL_REPO, pinned to MODEL_REVISION."""
    local = os.environ.get('MODEL_DIR')
    if local:
        return os.path.join(local, WEIGHTS_FILE), os.path.join(local, CONFIG_FILE)
    from huggingface_hub import hf_hub_download
    repo = os.environ['MODEL_REPO']
    rev = os.environ.get('MODEL_REVISION') or None
    tok = os.environ.get('HF_TOKEN')
    return (hf_hub_download(repo, WEIGHTS_FILE, revision=rev, token=tok),
            hf_hub_download(repo, CONFIG_FILE, revision=rev, token=tok))


def softmax(z):
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


class Verifier:
    def __init__(self, weights_path, config_path, expected_sha256=None):
        with open(config_path) as f:
            self.cfg = json.load(f)
        self.weights_sha256 = sha256_file(weights_path)
        if expected_sha256 and expected_sha256 != self.weights_sha256:
            raise RuntimeError('model weights hash does not match EXPECTED_WEIGHTS_SHA256 - refusing to serve')
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = int(os.environ.get('ORT_THREADS', '1'))
        self.session = ort.InferenceSession(weights_path, sess_options=opts, providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name
        self.version = f"verifier-v1+{self.weights_sha256[:12]}"
        self.threshold = float(self.cfg['auto_decision_threshold'])

    def logits(self, img):
        x = to_input(img, self.cfg['image_size'], self.cfg['normalize_mean'], self.cfg['normalize_std'])
        om, oc = self.session.run(None, {self.input_name: x})
        return om, oc

    def predict(self, img):
        om, oc = self.logits(img)
        pm = apply_temperature(softmax(om), self.cfg['temperature_material'])
        pc = apply_temperature(softmax(oc), self.cfg['temperature_clean'])
        decision, suggested, conf, status = final_decision(pm, pc, self.threshold)
        return {
            'decision': decision,
            'suggested_decision': suggested,
            'confidence': round(conf, 4),
            'status': status,
            'material': dict(zip(self.cfg['material_classes'], np.round(pm[0], 4).tolist(), strict=True)),
            'cleanliness': dict(zip(self.cfg['clean_classes'], np.round(pc[0], 4).tolist(), strict=True)),
        }
