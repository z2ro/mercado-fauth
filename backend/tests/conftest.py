import json
from pathlib import Path

import pytest

from backend.app.models.campaign import BannerRequest


@pytest.fixture
def payload():
    return json.loads((Path(__file__).resolve().parents[2] / 'examples/campaign.json').read_text())


@pytest.fixture
def request_model(payload):
    return BannerRequest.model_validate(payload)
