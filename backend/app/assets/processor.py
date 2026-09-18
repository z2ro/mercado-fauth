import base64
import hashlib
import json
import logging
import math
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import ASSET_SETTINGS, AssetSettings
from .background import (
    REMOVER_VERSION, BackgroundRemover, NoOpBackgroundRemover,
    RembgBackgroundRemover, model_bytes,
)

logger = logging.getLogger(__name__)
PIPELINE_VERSION = '2'
MAX_BYTES = 10_000_000
MAX_PIXELS = 20_000_000
MAX_SIDE = 600


@dataclass(frozen=True)
class PreparedAsset:
    data_uri: str
    width: int
    height: int

    @property
    def shape(self) -> str:
        ratio = self.width / self.height
        return 'vertical' if ratio < 0.7 else 'horizontal' if ratio > 1.5 else 'square'


def crop_to_content(image: Image.Image, padding_ratio: float = 0.04) -> Image.Image:
    if not math.isfinite(padding_ratio) or not 0 <= padding_ratio <= 0.25:
        raise ValueError('Padding deve estar entre 0 e 0.25.')
    rgba = image.convert('RGBA')
    bbox = rgba.getchannel('A').getbbox()
    if bbox is None:
        raise ValueError('Imagem completamente transparente.')
    cropped = rgba.crop(bbox)
    x = math.ceil(cropped.width * padding_ratio)
    y = math.ceil(cropped.height * padding_ratio)
    return ImageOps.expand(cropped, border=(x, y, x, y), fill=(0, 0, 0, 0))


def normalize(image: Image.Image, padding_ratio: float) -> Image.Image:
    normalized = crop_to_content(image, padding_ratio)
    normalized.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return normalized


def _prepared(png: bytes) -> PreparedAsset:
    with Image.open(BytesIO(png)) as image:
        if image.format != 'PNG' or image.mode != 'RGBA' or max(image.size) > MAX_SIDE:
            raise ValueError('Cache de asset inválido.')
        image.load()
        width, height = image.size
    return PreparedAsset('data:image/png;base64,' + base64.b64encode(png).decode('ascii'), width, height)


def _write_cache(path: Path, png: bytes) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(png)
        temporary.replace(path)
    except OSError:
        logger.warning('asset_cache_write_failed key=%s', path.stem)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_asset(
    image: str, asset_dir: Path, settings: AssetSettings | None = None,
    remover: BackgroundRemover | None = None,
) -> PreparedAsset | None:
    """Read bounded local raster bytes, normalize without changing the source."""
    settings = settings or ASSET_SETTINGS
    logger.info('asset_processing_started asset=%r', image)
    try:
        relative = Path(image)
        if '://' in image or relative.is_absolute():
            raise ValueError('Somente caminhos locais relativos são aceitos.')
        if relative.parts and relative.parts[0] == 'assets':
            relative = Path(*relative.parts[1:])
        path = (asset_dir / relative).resolve()
        if not path.is_relative_to(asset_dir.resolve()) or not path.is_file():
            raise ValueError('Asset fora da pasta permitida ou inexistente.')
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Asset maior que 10 MB.')
        model_digest = 'disabled'
        cacheable = True
        if settings.remove_background:
            try:
                model_digest = hashlib.sha256(model_bytes()).hexdigest() if remover is None else type(remover).__qualname__
            except OSError:
                model_digest, cacheable = 'unavailable', False
        parameters = json.dumps({
            'version': PIPELINE_VERSION, 'padding': settings.padding_ratio,
            'max_side': MAX_SIDE, 'remove_background': settings.remove_background,
            'remover': REMOVER_VERSION, 'model': model_digest,
        }, sort_keys=True).encode()
        key = hashlib.sha256(parameters + b'\0' + raw).hexdigest()
        cache = settings.cache_dir / f'{key}.png'
        if cacheable:
            try:
                result = _prepared(cache.read_bytes())
                logger.info('asset_cache_hit key=%s', key)
                return result
            except (OSError, ValueError):
                pass
        logger.info('asset_cache_miss key=%s', key)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.width * source.height > MAX_PIXELS:
                    raise ValueError('Asset excede 20 megapixels.')
                original = ImageOps.exif_transpose(source).convert('RGBA')
        selected = original
        if settings.remove_background:
            logger.info('background_removal_started key=%s', key)
            try:
                selected = (remover or RembgBackgroundRemover()).remove(original.copy())
                if not isinstance(selected, Image.Image) or selected.size != original.size:
                    raise ValueError('Remover retornou imagem inválida.')
                selected = selected.convert('RGBA')
                if selected.getchannel('A').getbbox() is None:
                    raise ValueError('Remover retornou imagem vazia.')
            except Exception as exc:
                # Do not cache failed removal as success; retry after model recovery.
                logger.warning('background_removal_failed key=%s error=%s', key, type(exc).__name__)
                selected, cacheable = original, False
        else:
            selected = NoOpBackgroundRemover().remove(original)
        normalized = normalize(selected, settings.padding_ratio)
        buffer = BytesIO()
        normalized.save(buffer, format='PNG')
        png = buffer.getvalue()
        if cacheable:
            _write_cache(cache, png)
        logger.info('asset_normalized key=%s width=%d height=%d', key, *normalized.size)
        return _prepared(png)
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        logger.warning('Imagem ausente ou inválida %r: %s', image, exc)
        return None


def product_image(image: str, asset_dir: Path) -> str | None:
    """Compatibility entrypoint for callers that only need the embedded PNG."""
    asset = prepare_asset(image, asset_dir)
    return asset.data_uri if asset else None
