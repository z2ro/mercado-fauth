import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = Path(os.getenv('BANNER_ASSET_DIR', ROOT / 'assets')).resolve()
OUTPUT_DIR = Path(os.getenv('BANNER_OUTPUT_DIR', ROOT / 'output')).resolve()
TEMPLATE_DIR = Path(__file__).parent / 'templates'
