"""Local CPU background removal. Models must be provisioned before serving."""
import os
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from PIL import Image

os.environ.setdefault('OMP_NUM_THREADS', '2')

MODEL_PATH = Path(os.getenv('BANNER_BACKGROUND_MODEL', '/opt/rembg/u2netp.onnx'))
REMOVER_VERSION = 'rembg-2.0.72-u2netp-v1'


class BackgroundRemover(Protocol):
    def remove(self, image: Image.Image) -> Image.Image: ...


class NoOpBackgroundRemover:
    def remove(self, image: Image.Image) -> Image.Image:
        return image


@lru_cache(maxsize=1)
def model_bytes() -> bytes:
    return MODEL_PATH.read_bytes()


@lru_cache(maxsize=1)
def local_session():
    # Custom session only reads this explicit local path; no automatic download.
    from rembg import new_session
    if not MODEL_PATH.is_file():
        raise FileNotFoundError('Modelo local ausente; configure BANNER_BACKGROUND_MODEL.')
    return new_session('u2net_custom', model_path=str(MODEL_PATH), providers=['CPUExecutionProvider'])


class RembgBackgroundRemover:
    def remove(self, image: Image.Image) -> Image.Image:
        from rembg import remove
        return remove(image, session=local_session())
