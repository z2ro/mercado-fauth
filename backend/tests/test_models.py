from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.app.models.campaign import BannerRequest
from backend.app.models.product import Product
from backend.app.renderer.renderer import format_price


def test_decimal_parsing(request_model):
    assert request_model.products[0].price == Decimal('39.90')
    assert isinstance(request_model.products[0].price, Decimal)


@pytest.mark.parametrize('price,expected', [('17.90', {'integer':'17','decimal':'90'}), ('0.01', {'integer':'0','decimal':'01'}), ('39', {'integer':'39','decimal':'00'}), ('999999.99', {'integer':'999999','decimal':'99'})])
def test_price_format(price, expected):
    assert format_price(Decimal(price)) == expected


@pytest.mark.parametrize('price', [17.90, Decimal('NaN'), Decimal('-1'), Decimal('1.999')])
def test_format_rejects_loss(price):
    with pytest.raises(ValueError):
        format_price(price)


@pytest.mark.parametrize('field,value', [('name',''), ('unit',' '), ('price','0'), ('price','-1'), ('price',17.90), ('price','1.999'), ('price','NaN'), ('category','unknown')])
def test_invalid_product(payload, field, value):
    payload['products'][0][field] = value
    with pytest.raises(ValidationError):
        Product.model_validate(payload['products'][0])


@pytest.mark.parametrize('count', [0, 1, 11, 13])
def test_product_count(payload, count):
    payload['products'] = (payload['products'] * 2)[:count]
    with pytest.raises(ValidationError):
        BannerRequest.model_validate(payload)


def test_duplicate_ids(payload):
    payload['products'][1]['id'] = payload['products'][0]['id']
    with pytest.raises(ValidationError):
        BannerRequest.model_validate(payload)


def test_immutable(request_model):
    with pytest.raises(ValidationError):
        request_model.products[0].price = Decimal('1')


def test_invalid_date(payload):
    payload['campaign']['valid_until'] = '31/02/2026'
    with pytest.raises(ValidationError):
        BannerRequest.model_validate(payload)
