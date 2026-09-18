from dataclasses import replace

import pytest
from pydantic import ValidationError

from backend.app.layout.affinity import affinity
from backend.app.layout.grid import GRID, adjacent, build_grid, manhattan
from backend.app.layout.planner import ImpossibleLayout, plan_layout
from backend.app.layout.rules import compatible, hard_rules_valid, layout_score
from backend.app.models.campaign import BannerRequest
from backend.app.models.design import DesignSpec


def test_grid():
    assert len(GRID) == 12
    assert [(s.index, s.x, s.y) for s in GRID] == [(i, i % 4, i // 4) for i in range(12)]
    assert len(build_grid(2, 2)) == 4


def test_manhattan():
    assert manhattan(GRID[0], GRID[11]) == 5
    assert manhattan(GRID[0], GRID[0]) == 0


def test_adjacency():
    assert adjacent(GRID[0], GRID[1])
    assert adjacent(GRID[0], GRID[4])
    assert not adjacent(GRID[0], GRID[5])
    assert not adjacent(GRID[3], GRID[4])


@pytest.mark.parametrize('category', ['acougue', 'hortifruti', 'padaria'])
def test_hard_rules(category, request_model):
    assert not compatible(category, 'limpeza')
    assert not compatible('limpeza', category)
    products = [p.model_copy(update={'category':'higiene'}) for p in request_model.products]
    products[0] = products[0].model_copy(update={'category':category})
    products[4] = products[4].model_copy(update={'category':'limpeza'})
    assert not hard_rules_valid(products)
    products[4], products[5] = products[5], products[4]
    assert hard_rules_valid(products)


def test_affinity():
    assert affinity('acougue','frios') == affinity('frios','acougue') == 3
    assert affinity('hortifruti','limpeza') == -10
    assert affinity('bebidas','padaria') == 0
    assert affinity('limpeza','limpeza') > affinity('limpeza','higiene')


def test_planner_deterministic(request_model):
    assert plan_layout(request_model) == plan_layout(request_model)
    reversed_request = request_model.model_copy(update={'products':tuple(reversed(request_model.products))})
    assert plan_layout(request_model) == plan_layout(reversed_request)


def test_complete_design(request_model):
    spec = plan_layout(request_model)
    assert len(spec.products) == len({p.product_id for p in spec.products}) == 12
    assert {p.product_id for p in spec.products} == {p.id for p in request_model.products}
    assert {p.slot for p in spec.products} == set(range(12))
    assert all(p.x == p.slot % 4 and p.y == p.slot // 4 for p in spec.products)
    original = {p.id:p for p in request_model.products}
    assert hard_rules_valid([original[p.product_id] for p in spec.products])
    assert [p.product_id for p in spec.products] != [p.id for p in request_model.products]
    assert not {'price','name','sku','unit'} & set(spec.products[0].model_dump())


def test_impossible(payload):
    for p in payload['products']:
        p['category'] = 'acougue'
    payload['products'][0]['category'] = 'limpeza'
    with pytest.raises(ImpossibleLayout):
        plan_layout(BannerRequest.model_validate(payload))


@pytest.mark.parametrize('category', ['limpeza','acougue','hortifruti','padaria'])
def test_single_category(payload, category):
    for p in payload['products']:
        p['category'] = category
    request = BannerRequest.model_validate(payload)
    spec = plan_layout(request)
    assert len(spec.products) == 12
    featured = next(p for p in spec.products if p.featured)
    assert featured.slot in (0,3)


def test_design_rejects_duplicate_slots(request_model):
    data = plan_layout(request_model).model_dump()
    data['products'][1]['slot'] = 0
    with pytest.raises(ValidationError):
        DesignSpec.model_validate(data)


def test_grouping_score(request_model):
    base = request_model.products[0]
    grouped = [base.model_copy(update={'category': 'mercearia' if i < 8 else 'bebidas', 'featured':False}) for i in range(12)]
    fragmented = [grouped[i] for i in (0,8,1,9,2,3,10,4,11,5,6,7)]
    assert layout_score(grouped) > layout_score(fragmented)
