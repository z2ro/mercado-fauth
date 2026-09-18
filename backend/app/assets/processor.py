import base64
import logging
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)


def product_image(image: str, asset_dir: Path) -> str | None:
    """Normalize local raster assets; never allow file access outside assets/."""
    try:
        relative = Path(image)
        if relative.parts and relative.parts[0] == 'assets':
            relative = Path(*relative.parts[1:])
        path = (asset_dir / relative).resolve()
        if not path.is_relative_to(asset_dir.resolve()) or path.stat().st_size > 10_000_000:
            raise ValueError('Asset fora da pasta permitida ou maior que 10 MB')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if source.width * source.height > 20_000_000:
                    raise ValueError('Asset excede 20 megapixels')
                normalized = ImageOps.exif_transpose(source).convert('RGBA')
                normalized.thumbnail((600, 600))
                buffer = BytesIO()
                normalized.save(buffer, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        logger.warning('Imagem ausente ou inválida %r: %s', image, exc)
        return None
