import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

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




class ClassificationSettings(BaseModel):
    model_config = ConfigDict(frozen=True)
    enabled: bool = False
    provider: str = ''
    model: str = ''
    api_key: SecretStr = SecretStr('')
    min_confidence: float = Field(default=0.70, ge=0, le=1, allow_inf_nan=False)
    timeout_seconds: float = Field(default=15, gt=0, le=120, allow_inf_nan=False)
    cache_dir: Path = ROOT / '.cache' / 'classifications'

    @field_validator('cache_dir', mode='before')
    @classmethod
    def valid_cache(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError('Cache de classificação não pode ser vazio.')
        return Path(value).resolve()


def classification_settings() -> ClassificationSettings:
    return ClassificationSettings(
        enabled=os.getenv('BANNER_AI_CLASSIFICATION_ENABLED', 'false'),
        provider=os.getenv('BANNER_AI_PROVIDER', ''),
        model=os.getenv('BANNER_AI_MODEL', ''),
        api_key=os.getenv('BANNER_AI_API_KEY', ''),
        min_confidence=os.getenv('BANNER_CLASSIFICATION_MIN_CONFIDENCE', '0.70'),
        timeout_seconds=os.getenv('BANNER_AI_TIMEOUT_SECONDS', '15'),
        cache_dir=os.getenv('BANNER_CLASSIFICATION_CACHE_DIR', str(ROOT / '.cache' / 'classifications')),
    )


CLASSIFICATION_SETTINGS = classification_settings()
