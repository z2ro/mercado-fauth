import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = Path(os.getenv('BANNER_ASSET_DIR', ROOT / 'assets')).resolve()
OUTPUT_DIR = Path(os.getenv('BANNER_OUTPUT_DIR', ROOT / 'output')).resolve()
TEMPLATE_DIR = Path(__file__).parent / 'templates'



class AssetSettings(BaseModel):
    model_config = ConfigDict(frozen=True)
    remove_background: bool = False
    cache_dir: Path = ROOT / '.cache' / 'assets'
    padding_ratio: float = Field(default=0.04, ge=0, le=0.25, allow_inf_nan=False)

    @field_validator('cache_dir', mode='before')
    @classmethod
    def valid_directory(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError('BANNER_ASSET_CACHE_DIR não pode ser vazio.')
        return Path(value).resolve()


def asset_settings() -> AssetSettings:
    return AssetSettings(
        remove_background=os.getenv('BANNER_REMOVE_BACKGROUND', 'false'),
        cache_dir=os.getenv('BANNER_ASSET_CACHE_DIR', str(ROOT / '.cache' / 'assets')),
        padding_ratio=os.getenv('BANNER_ASSET_PADDING_RATIO', '0.04'),
    )


ASSET_SETTINGS = asset_settings()
