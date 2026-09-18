import asyncio
import json
import logging
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from backend.app import main
from backend.app.classification import cache
from backend.app.classification.llm import LLMProductClassifier
from backend.app.classification.models import ClassificationBatch, ProductClassification, ProductClassificationInput
from backend.app.classification.resolver import UnresolvedClassification, resolve_products
from backend.app.classification.rules import RuleBasedProductClassifier
from backend.app.config import ClassificationSettings, ROOT, classification_settings
from backend.app.layout.planner import plan_layout
from backend.app.layout.rules import hard_rules_valid, rectangles_hard_rules_valid
from backend.app.models.campaign import BannerRequest, ResolvedBannerRequest


def run(awaitable):
    return asyncio.run(awaitable)


class FakeProductClassifier:
    def __init__(self, mode='ok', confidence=.97):
        self.calls=[]
        self.mode=mode
        self.confidence=confidence

    async def classify(self, product):
        return (await self.classify_batch((product,))).products[0]

    async def classify_batch(self, products):
        self.calls.append(products)
        if self.mode=='failure':
            raise RuntimeError('SECRET_API_KEY')
        if self.mode=='timeout':
            await asyncio.sleep(1)
        results=[dict(product_id=p.product_id,category='mercearia',subcategory='outros',confidence=self.confidence) for p in products]
        if self.mode=='missing':
            results.pop()
        if self.mode=='duplicate':
            results.append(results[0])
        if self.mode=='unknown':
            results[0]['product_id']='unknown'
        if self.mode=='commercial':
            results[0]['price']='0.01'
        return ClassificationBatch.model_validate({'products':results})


@pytest.fixture
def settings(tmp_path):
    return ClassificationSettings(enabled=True,provider='openai',model='test-model',api_key='SECRET_API_KEY',cache_dir=tmp_path/'classifications',timeout_seconds=.05)


@pytest.fixture
def unknown(payload):
    payload['products'][0].pop('category')
    payload['products'][0].pop('subcategory')
    payload['products'][0]['name']='Produto ZZZ'
    return BannerRequest.model_validate(payload)


@pytest.mark.parametrize('name,category', [('Paleta bovina','acougue'),('Queijo','frios'),('Maçã','hortifruti'),('Coca-Cola','bebidas'),('Água sanitária','limpeza'),('Shampoo','higiene'),('Molho de tomate','mercearia'),('Pão francês','padaria'),('Pão de queijo','padaria')])
def test_keywords(name,category):
    result=run(RuleBasedProductClassifier().classify(ProductClassificationInput(product_id='p',name=name,unit='UN')))
    assert result.category==category


@pytest.mark.parametrize('name',['sabonetex','produto zzz','queijo e detergente'])
def test_no_unsafe_rule(name):
    with pytest.raises(ValueError):
        run(RuleBasedProductClassifier().classify(ProductClassificationInput(product_id='p',name=name,unit='UN')))


def test_manual_no_ai(request_model,settings):
    fake=FakeProductClassifier()
    resolved,meta=run(resolve_products(request_model,settings,fake))
    assert not fake.calls
    assert resolved.model_dump()==request_model.model_dump()
    assert all(m.source=='user' for m in meta.values())


def test_manual_missing_subcategory(payload,settings):
    payload['products'][0].pop('subcategory')
    fake=FakeProductClassifier()
    resolved,_=run(resolve_products(BannerRequest.model_validate(payload),settings,fake))
    assert resolved.products[0].subcategory=='carne_bovina'
    assert not fake.calls


def test_manual_optional_unresolved_subcategory(payload,settings):
    payload['products'][0].pop('subcategory')
    payload['products'][0]['name']='ZZZ'
    fake=FakeProductClassifier()
    resolved,_=run(resolve_products(BannerRequest.model_validate(payload),settings,fake))
    assert resolved.products[0].subcategory is None
    assert not fake.calls


def test_subcategory_preserved(unknown,settings):
    request=unknown.model_copy(update={'products':(unknown.products[0].model_copy(update={'subcategory':'Escolha Manual'}),*unknown.products[1:])})
    resolved,_=run(resolve_products(request,settings,FakeProductClassifier()))
    assert resolved.products[0].subcategory=='Escolha Manual'


def test_cache_hit_miss_price_independent(unknown,settings,caplog):
    caplog.set_level(logging.INFO)
    fake=FakeProductClassifier()
    first,_=run(resolve_products(unknown,settings,fake))
    changed=unknown.model_copy(update={'products':(unknown.products[0].model_copy(update={'price':Decimal('123.45'),'id':'new'}),*unknown.products[1:])})
    second,meta=run(resolve_products(changed,settings,fake))
    assert len(fake.calls)==1
    assert meta['new'].source=='cache'
    assert second.products[0].price==Decimal('123.45')
    assert first.products[0].category==second.products[0].category
    assert 'classification_cache_miss' in caplog.text and 'classification_cache_hit' in caplog.text


@pytest.mark.parametrize('field,value',[('model','other'),('provider','other'),('enabled',False)])
def test_key_config(settings,field,value):
    item=ProductClassificationInput(product_id='p',name='Água',unit='UN')
    assert cache.cache_key(item,settings)!=cache.cache_key(item,settings.model_copy(update={field:value}))


def test_cache_normalization(settings):
    a=ProductClassificationInput(product_id='p',name='  ÁGUA ',unit='UN')
    b=ProductClassificationInput(product_id='q',name='agua',unit='un')
    assert cache.cache_key(a,settings)==cache.cache_key(b,settings)


def test_cache_threshold_rechecked(unknown,settings):
    run(resolve_products(unknown,settings,FakeProductClassifier(confidence=.8)))
    with pytest.raises(UnresolvedClassification):
        run(resolve_products(unknown,settings.model_copy(update={'min_confidence':.9}),FakeProductClassifier(confidence=.8)))


def test_corrupt_cache(unknown,settings):
    fake=FakeProductClassifier()
    run(resolve_products(unknown,settings,fake))
    next(settings.cache_dir.glob('*.json')).write_text('{invalid')
    run(resolve_products(unknown,settings,fake))
    assert len(fake.calls)==2


def test_disabled(unknown,settings):
    fake=FakeProductClassifier()
    with pytest.raises(UnresolvedClassification):
        run(resolve_products(unknown,settings.model_copy(update={'enabled':False}),fake))
    assert not fake.calls


@pytest.mark.parametrize('mode',['failure','timeout','missing','duplicate','unknown','commercial'])
def test_failure_and_bad_batch(unknown,settings,mode,caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(UnresolvedClassification) as exc:
        run(resolve_products(unknown,settings,FakeProductClassifier(mode)))
    assert exc.value.ids==['p001']
    assert 'classification_ai_failed' in caplog.text
    assert 'SECRET_API_KEY' not in caplog.text
    assert not list(settings.cache_dir.glob('*.json'))


def test_low_confidence(unknown,settings,caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(UnresolvedClassification):
        run(resolve_products(unknown,settings,FakeProductClassifier(confidence=.2)))
    assert 'classification_low_confidence' in caplog.text


@pytest.mark.parametrize('mode,confidence',[('failure',.97),('timeout',.97),('ok',.2)])
def test_fallback_after_ai(unknown,settings,mode,confidence):
    request=unknown.model_copy(update={'products':(unknown.products[0].model_copy(update={'name':'Iogurte'}),*unknown.products[1:])})
    fake=FakeProductClassifier(mode,confidence)
    resolved,meta=run(resolve_products(request,settings,fake))
    assert len(fake.calls)==1
    assert resolved.products[0].category=='frios'
    assert meta['p001'].source=='rule'


@pytest.mark.parametrize('field,value',[('category','arbitrary'),('confidence',-1),('confidence',1.1),('confidence','0.9'),('confidence',True),('confidence',float('nan')),('subcategory','texto livre')])
def test_schema_rejects(field,value):
    data=dict(product_id='p',category='acougue',subcategory='aves',confidence=.95)
    data[field]=value
    with pytest.raises(ValidationError):
        ProductClassification.model_validate(data)


@pytest.mark.parametrize('field',['price','sku','name','unit','image','featured'])
def test_output_cannot_change_commercial(field):
    data=dict(product_id='p',category='acougue',subcategory='aves',confidence=.95)
    data[field]='ATTACK'
    with pytest.raises(ValidationError):
        ProductClassification.model_validate(data)


def test_batch_twelve_and_immutable(payload,settings):
    for p in payload['products']:
        p.pop('category');p.pop('subcategory');p['name']='ZZZ '+p['id']
    request=BannerRequest.model_validate(payload)
    before=request.model_dump_json()
    fake=FakeProductClassifier()
    resolved,meta=run(resolve_products(request,settings,fake))
    assert len(fake.calls)==1 and len(fake.calls[0])==12
    assert set(fake.calls[0][0].model_dump())=={'product_id','name','unit'}
    for old,new in zip(request.products,resolved.products):
        assert old.model_dump(exclude={'category','subcategory'})==new.model_dump(exclude={'category','subcategory'})
    assert before==request.model_dump_json()
    assert isinstance(resolved,ResolvedBannerRequest)


def test_mixed(payload,settings):
    payload['products'][1].pop('category')
    payload['products'][2].pop('category');payload['products'][2]['name']='ZZZ'
    fake=FakeProductClassifier()
    _,meta=run(resolve_products(BannerRequest.model_validate(payload),settings,fake))
    assert meta['p001'].source=='user'
    assert meta['p002'].source=='rule'
    assert meta['p003'].source=='ai'
    assert [p.product_id for p in fake.calls[0]]==['p003']


def test_missing_credentials_fallback(unknown,settings):
    with pytest.raises(UnresolvedClassification):
        run(resolve_products(unknown,settings.model_copy(update={'api_key':ClassificationSettings().api_key})))


@pytest.mark.parametrize('mode',['ok','json','category','confidence','refusal','incomplete','http'])
def test_llm_http_schema_offline(settings,mode):
    item=ProductClassificationInput(product_id='p',name='ZZZ',unit='UN')
    def handler(request):
        body=json.loads(request.content)
        assert body['text']['format']['strict'] is True
        assert body['store'] is False
        assert json.loads(body['input'])=={'products':[item.model_dump()]}
        assert request.extensions['timeout']['read']==settings.timeout_seconds
        result={'products':[dict(product_id='p',category='mercearia',subcategory=None,confidence=.9)]}
        if mode=='category':result['products'][0]['category']='invalid'
        if mode=='confidence':result['products'][0]['confidence']=2
        envelope={'status':'incomplete' if mode=='incomplete' else 'completed','output':[{'type':'message','content':[{'type':'refusal' if mode=='refusal' else 'output_text','text':'not json' if mode=='json' else json.dumps(result)}]}]}
        return httpx.Response(500 if mode=='http' else 200,json=envelope)
    llm=LLMProductClassifier(settings,httpx.MockTransport(handler))
    if mode=='ok':
        assert run(llm.classify(item)).category=='mercearia'
    else:
        with pytest.raises((ValueError,httpx.HTTPError)):
            run(llm.classify(item))


@pytest.mark.parametrize('name,value',[('BANNER_CLASSIFICATION_MIN_CONFIDENCE','NaN'),('BANNER_CLASSIFICATION_MIN_CONFIDENCE','2'),('BANNER_AI_TIMEOUT_SECONDS','0'),('BANNER_AI_CLASSIFICATION_ENABLED','nonsense'),('BANNER_CLASSIFICATION_CACHE_DIR','')])
def test_config_validation(monkeypatch,name,value):
    monkeypatch.setenv(name,value)
    with pytest.raises(ValidationError):classification_settings()


def test_auto_banner_offline(tmp_path,monkeypatch):
    payload=json.loads((ROOT/'examples/campaign-auto-classification.json').read_text())
    request=BannerRequest.model_validate(payload)
    resolved,meta=run(resolve_products(request))
    spec=plan_layout(resolved)
    by_id={p.id:p for p in resolved.products}
    if hasattr(spec, 'placements'):
        assert rectangles_hard_rules_valid(spec.placements, by_id)
    else:
        assert hard_rules_valid([by_id[p.product_id] for p in spec.products])
    assert all(m.source=='rule' for m in meta.values())
    for old,new in zip(request.products,resolved.products):
        assert old.model_dump(exclude={'category','subcategory'})==new.model_dump(exclude={'category','subcategory'})
    monkeypatch.setattr(main,'OUTPUT_DIR',tmp_path)
    response=TestClient(main.app).post('/api/v1/banners',json=payload)
    assert response.status_code==201,response.text
    assert all(m['source']=='cache' for m in response.json()['classification'].values())
    with Image.open(tmp_path/Path(response.json()['file']).name) as image:
        assert image.size==(1080,1080)


def test_api_unresolved(unknown):
    result=TestClient(main.app).post('/api/v1/banners',json=unknown.model_dump(mode='json'))
    assert result.status_code==422
    assert 'p001' in result.json()['detail']


def test_unicode_cache_names_do_not_collapse(settings):
    a=ProductClassificationInput(product_id='p',name='茶',unit='UN')
    b=ProductClassificationInput(product_id='p',name='米',unit='UN')
    assert cache.cache_key(a,settings)!=cache.cache_key(b,settings)
