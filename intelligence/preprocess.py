"""Image preprocessing in plain numpy + PIL, reproducing the training pipeline
    torchvision: Resize(256) -> CenterCrop(224) -> ToTensor() -> Normalize(mean, std)
without needing torch at serving time. The deploy notebook checks that this gives
the same tensor as torchvision on real test images.
"""
import numpy as np
from PIL import Image


def resize_shorter_side(img, size):
    # torchvision rule: shorter side -> size, longer side -> int(size * long / short)
    w, h = img.size
    if w <= h:
        new_w, new_h = size, int(size * h / w)
    else:
        new_w, new_h = int(size * w / h), size
    return img.resize((new_w, new_h), Image.BILINEAR)


def center_crop(img, size):
    # torchvision rule: offsets are int(round((dim - size) / 2.0))
    w, h = img.size
    top = round((h - size) / 2.0)
    left = round((w - size) / 2.0)
    return img.crop((left, top, left + size, top + size))


def to_input(img, image_size, mean, std, resize_to=256):
    """RGB PIL image -> float32 array of shape (1, 3, image_size, image_size)."""
    img = center_crop(resize_shorter_side(img, resize_to), image_size)
    x = np.asarray(img, dtype=np.float32) / 255.0
    x = (x - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None])
