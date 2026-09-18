import asyncio
import csv
import json
from pathlib import Path

import pytest
import httpx
from pydantic import ValidationError

from backend.app.classification.llm import LLMProductClassifier
from backend.app.classification.models import ProductClassificationInput
from backend.app.config import ClassificationSettings, ROOT
from eval.dataset import EvalProduct, load_dataset
from eval.evaluator import chunks, evaluate_ai, evaluate_hybrid, evaluate_rule, rule_classification_flags
from eval.fake_provider import FakeProductClassifier
from eval.metrics import (CATEGORIES, confidence_analysis, confusion_matrix, grouped_accuracy,
                          percentile, repeatability, rule_known_analysis, score_rows, subcategory_accuracy)
from eval.reporting import write_outputs
from eval.run_eval import clear_eval_cache, main, provider_status

DATASET=ROOT/'eval/datasets/products.json'


@pytest.fixture
def rows():
    return [
        {'product_id':'1','expected_category':'acougue','predicted_category':'acougue','candidate_category':'acougue','difficulty':'easy','rule_known':True,'confidence':.95,'candidate_correct':True,'correct':True,'resolved':True,'expected_subcategory':'carnes','predicted_subcategory':'carnes'},
        {'product_id':'2','expected_category':'acougue','predicted_category':'frios','candidate_category':'frios','difficulty':'hard','rule_known':False,'confidence':.55,'candidate_correct':False,'correct':False,'resolved':True,'expected_subcategory':'carnes','predicted_subcategory':'queijos'},
        {'product_id':'3','expected_category':'frios','predicted_category':None,'candidate_category':None,'difficulty':'medium','rule_known':False,'confidence':None,'candidate_correct':False,'correct':False,'resolved':False,'expected_subcategory':'embutidos','predicted_subcategory':None},
    ]


def test_dataset_valid():
    dataset,_=load_dataset(DATASET)
    assert len(dataset)==120
    assert sum(p.difficulty=='easy' for p in dataset)==40


@pytest.mark.parametrize('mutation,message',[('duplicate','IDs duplicados'),('category','validation error'),('difficulty','validation error'),('empty_id','validation error'),('empty_name','validation error'),('empty_unit','validation error'),('subcategory','validation error')])
def test_dataset_invalid(tmp_path,mutation,message):
    row={'id':'x','name':'Produto','unit':'UN','expected_category':'frios','expected_subcategory':'queijos','difficulty':'easy'}
    data=[row.copy(),dict(row,id='y')]
    if mutation=='duplicate':data[1]['id']='x'
    elif mutation=='category':data[0]['expected_category']='outros'
    elif mutation=='difficulty':data[0]['difficulty']='trivial'
    elif mutation=='empty_id':data[0]['id']='  '
    elif mutation=='empty_name':data[0]['name']=' '
    elif mutation=='empty_unit':data[0]['unit']=''
    elif mutation=='subcategory':data[0]['expected_subcategory']='Queijos frescos'
    path=tmp_path/'data.json';path.write_text(json.dumps(data))
    with pytest.raises(ValueError):load_dataset(path)


def test_dataset_id_empty(tmp_path):
    path=tmp_path/'empty.json';path.write_text(json.dumps([{'id':'','name':'X','unit':'UN','expected_category':'frios','difficulty':'easy'}]))
    with pytest.raises(ValueError,match='Dataset inválido'):load_dataset(path)


def test_scores_and_macro(rows):
    result=score_rows(rows)
    assert result['accuracy']==pytest.approx(1/3)
    assert result['resolved_products']==2 and result['unresolved_products']==1
    assert result['category_metrics']['acougue']['precision']==pytest.approx(1)
    assert result['category_metrics']['acougue']['recall']==pytest.approx(.5)
    assert result['category_metrics']['acougue']['f1']==pytest.approx(2/3)
    assert result['macro_f1']==pytest.approx(sum(x['f1'] for x in result['category_metrics'].values())/8)


def test_zero_division():
    assert score_rows([])['accuracy']==0
    assert score_rows([])['macro_f1']==0


def test_confusion_and_unresolved(rows):
    matrix=confusion_matrix(rows)
    assert len(matrix)==8 and matrix['acougue']['frios']==1 and matrix['frios']['unresolved']==1


def test_confidence_bands_threshold(rows):
    result=confidence_analysis(rows)
    assert result['bins'][0]['count']==0
    assert result['bins'][1]['count']==1
    assert result['bins'][-1]['correct']==1
    assert len(result['thresholds'])==8
    low=result['thresholds'][0];high=result['thresholds'][-1]
    assert low['accepted']==2 and low['coverage']==pytest.approx(2/3)
    assert high['accepted']==1 and high['accuracy_among_accepted']==1


def test_confidence_fractional_edge():
    row={'confidence':.499,'candidate_correct':True,'correct':False,'resolved':False,'predicted_category':None,'expected_category':'acougue'}
    assert confidence_analysis([row])['bins'][0]['count']==1


def test_confidence_bin_boundaries():
    values=(.49,.50,.59,.60,.69,.70,.79,.80,.89,.90,.99,1.0)
    rows=[{'confidence':value,'candidate_correct':True,'correct':True} for value in values]
    assert [item['count'] for item in confidence_analysis(rows)['bins']]==[1,2,2,2,2,3]


def test_rule_known_and_difficulty(rows):
    known=rule_known_analysis(rows)
    assert known['rule_known_count']==1 and known['rule_unknown_count']==2
    assert known['accuracy_rule_known']==1
    difficulty=grouped_accuracy(rows,'difficulty',('easy','medium','hard'))
    assert difficulty['easy']['accuracy']==1 and difficulty['hard']['accuracy']==0


def test_rule_matched_and_strong_are_distinct():
    from backend.app.models.product import Category
    products=(
        EvalProduct(id='moderate',name='Iogurte natural',unit='UN',expected_category=Category.FRIOS,
                    expected_subcategory='laticinios',difficulty='medium'),
        EvalProduct(id='strong',name='Paleta bovina',unit='KG',expected_category=Category.ACOUGUE,
                    expected_subcategory='carne_bovina',difficulty='easy'),
    )
    flags=asyncio.run(rule_classification_flags(products,.70))
    assert flags=={'moderate':{'matched':True,'strong':False},'strong':{'matched':True,'strong':True}}


@pytest.mark.parametrize('provider,available,modes,calls,successes,expected,reason',[
    ('openai',False,('ai',),0,0,'NOT_RUN','credentials_missing'),
    ('openai',True,('rule',),0,0,'NOT_RUN','provider_mode_not_requested'),
    ('openai',True,('ai',),0,0,'NOT_RUN','no_external_provider_batches'),
    ('openai',True,('ai',),2,0,'ATTEMPTED_FAILED',None),
    ('openai',True,('ai','hybrid'),3,1,'COMPLETED',None),
    ('fake',True,('ai',),3,3,'NOT_RUN','fake_provider_is_not_real'),
])
def test_real_provider_status(provider,available,modes,calls,successes,expected,reason):
    state,why=provider_status(provider,available,modes,
        {'provider_call_count':calls,'provider_success_count':successes},reason)
    assert state==expected
    assert why==reason


def test_subcategory_exact_and_unresolved(rows):
    assert subcategory_accuracy(rows)==pytest.approx(1/3)
    assert subcategory_accuracy([dict(rows[0],expected_subcategory=None)]) is None


@pytest.mark.parametrize('percent,expected',[(50,2),(95,2.9),(0,1),(100,3)])
def test_percentile(percent,expected):
    assert percentile([3,1,2],percent)==pytest.approx(expected)


def test_percentile_empty_and_bad():
    assert percentile([],50) is None
    with pytest.raises(ValueError):percentile([1],101)


def test_batch_splitting():
    assert [len(x) for x in chunks(tuple(range(25)),12)]==[12,12,1]
    assert [len(x) for x in chunks(tuple(range(5)),4)]==[4,1]
    with pytest.raises(ValueError):chunks((1,),0)


def test_fake_is_deterministic_and_marked():
    fake=FakeProductClassifier();item=ProductClassificationInput(product_id='x',name='Not meat',unit='UN')
    a=asyncio.run(fake.classify(item));b=asyncio.run(fake.classify(item))
    assert a==b and a.category=='mercearia' and a.confidence==.8
    assert 'Infrastructure fixture only' in FakeProductClassifier.__doc__


def test_repeat_runs_and_flips(rows):
    run_a=[dict(rows[0]),dict(rows[1])]
    run_b=[dict(rows[0]),dict(rows[1],predicted_category='acougue')]
    result=repeatability([run_a,run_b])
    assert result['agreement_rate']==.5 and result['category_flip_count']==1
    assert repeatability([run_a])['agreement_rate'] is None


def test_cache_isolation_no_cache_and_clear(tmp_path,monkeypatch):
    import eval.run_eval as cli
    root=tmp_path
    monkeypatch.setattr(cli,'ROOT',root)
    cache=root/'.cache'/'eval-classifications';cache.mkdir(parents=True)
    (cache/'fixture.json').write_text('{}')
    clear_eval_cache(cache)
    assert not cache.exists()
    other=root/'.cache'/'classifications';other.mkdir(parents=True);(other/'keep').write_text('safe')
    with pytest.raises(ValueError):clear_eval_cache(other)
    assert (other/'keep').exists()
    monkeypatch.setattr(cli,'EVAL_CACHE_DIR',cache)


def test_no_cache_does_not_write_eval_or_production_cache(tmp_path):
    dataset,_=load_dataset(DATASET)
    settings=ClassificationSettings(enabled=True,provider='fake',model='test',cache_dir=tmp_path/'eval')
    from eval.evaluator import rule_known_flags
    flags=asyncio.run(rule_known_flags(dataset,.7))
    result=asyncio.run(evaluate_ai(dataset,flags,24,1,settings,'fake',False))
    assert result['latency']['batch_count']==5
    assert not (tmp_path/'eval').exists()


def test_eval_cache_hit_isolated_from_production_cache(tmp_path):
    dataset,_=load_dataset(DATASET)
    sample=dataset[:6]
    settings=ClassificationSettings(enabled=True,provider='fake',model='test',cache_dir=tmp_path/'eval-classifications')
    flags={p.id:False for p in sample}
    first=asyncio.run(evaluate_ai(sample,flags,4,1,settings,'fake',True))
    second=asyncio.run(evaluate_ai(sample,flags,4,1,settings,'fake',True))
    assert all(row['source']=='ai' for row in first['rows'])
    assert all(row['source']=='cache' for row in second['rows'])
    assert len(list((tmp_path/'eval-classifications').glob('*.json')))==len(sample)
    assert first['latency']['evaluation_batch_count']==2
    assert first['latency']['provider_call_count']==2
    assert first['latency']['provider_success_count']==2
    assert first['latency']['provider_failure_count']==0
    assert first['latency']['provider_latency_p50'] is not None
    assert second['latency']['evaluation_batch_count']==2
    assert second['latency']['provider_call_count']==0
    assert second['latency']['provider_latency_p95'] is None


def test_openai_usage_collection_uses_production_classifier_offline():
    settings=ClassificationSettings(enabled=True,provider='openai',model='test',api_key='secret')
    item=ProductClassificationInput(product_id='p',name='Produto',unit='UN')
    def handler(request):
        body=json.loads(request.content)
        assert set(json.loads(body['input'])['products'][0])=={'product_id','name','unit'}
        assert 'secret' not in body['input']
        payload={'products':[{'product_id':'p','category':'mercearia','subcategory':'mercearia_geral','confidence':.9}]}
        return httpx.Response(200,json={'status':'completed','usage':{'input_tokens':20,'output_tokens':10,'total_tokens':30},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(payload)}]}]})
    classifier=LLMProductClassifier(settings,httpx.MockTransport(handler))
    asyncio.run(classifier.classify_batch((item,)))
    assert classifier.last_usage=={'input_tokens':20,'output_tokens':10,'total_tokens':30}


def test_provider_failure_is_recorded(tmp_path,monkeypatch):
    import eval.evaluator as evaluator
    class Failure:
        async def classify_batch(self,products):raise RuntimeError('offline failure')
    dataset,_=load_dataset(DATASET)
    settings=ClassificationSettings(enabled=True,provider='openai',model='test',api_key='bad',cache_dir=tmp_path/'eval',timeout_seconds=.01)
    monkeypatch.setattr(evaluator,'LLMProductClassifier',lambda settings:Failure())
    result=asyncio.run(evaluator.evaluate_ai(dataset[:4],{p.id:False for p in dataset[:4]},4,1,settings,'openai',False))
    assert result['unresolved_products']==4 and result['usage'] is None
    assert result['latency']['evaluation_batch_count']==1
    assert result['latency']['provider_call_count']==1
    assert result['latency']['provider_success_count']==0
    assert result['latency']['provider_failure_count']==1
    assert result['latency']['provider_average_latency'] is not None


def test_report_files_consistent(tmp_path,rows):
    matrix=confusion_matrix(rows)
    report={'configuration':{'provider':'fake','model':'fixed','batch_size':3,'runs':1,'fake_is_not_quality_eval':True},
        'dataset':{'total_products':3,'rule_known_count':1,'rule_unknown_count':2},'rule_based':None,'ai':None,'hybrid':None,
        'confusion_matrix':matrix,'confusion_matrix_mode':'hybrid','confidence_analysis':{'bins':[],'thresholds':[]},'latency':{},'usage':None,'repeatability':{},'observations':['3 produtos observados.']}
    errors=[{'run':1,'mode':'hybrid','product_id':'2','name':'B','difficulty':'hard','rule_known':False,'expected':'acougue','predicted':'frios','candidate_category':'frios','confidence':.98,'source':'ai','unresolved':False}]
    output=tmp_path/'run';write_outputs(output,report,errors,{'hybrid':rows})
    assert json.loads((output/'report.json').read_text())['confusion_matrix']==matrix
    assert len(json.loads((output/'errors.json').read_text()))==1
    with (output/'predictions.csv').open() as stream:assert len(list(csv.DictReader(stream)))==3
    with (output/'confusion-matrix.csv').open() as stream:assert len(list(csv.reader(stream)))==9
    md=(output/'report.md').read_text()
    for section in ('## Configuration','## Dataset','## Rule Based','## AI','## Hybrid','## Category Metrics','## Difficulty','## Rule Known vs Unknown','## Confusion Matrix','## Confidence Analysis','## Latency','## Provider Usage','## Errors','## Repeated Runs','## Observations'):
        assert section in md
    assert 'Primary mode: `hybrid`' in md
    with pytest.raises(FileExistsError):write_outputs(output,report,[],{})


def test_errors_sorted_by_confidence(tmp_path,rows):
    report={'configuration':{'provider':'fake','model':'fixed','batch_size':1,'runs':1,'fake_is_not_quality_eval':True},'dataset':{'total_products':3,'rule_known_count':1,'rule_unknown_count':2},'rule_based':None,'ai':None,'hybrid':None,'confusion_matrix':confusion_matrix(rows),'confusion_matrix_mode':'hybrid','confidence_analysis':{'bins':[],'thresholds':[]},'latency':{},'usage':None,'repeatability':{},'observations':[]}
    errors=[]
    for confidence,product_id in ((.3,'low'),(.99,'high')):
        errors.append({'run':1,'mode':'ai','product_id':product_id,'name':'X','difficulty':'hard','rule_known':False,'expected':'acougue','predicted':'frios','candidate_category':'frios','confidence':confidence,'source':'ai','unresolved':False})
    output=tmp_path/'errors';write_outputs(output,report,errors,{})
    saved=json.loads((output/'errors.json').read_text())
    assert [r['product_id'] for r in saved]==['high','low']


def test_clear_cache_cli_and_batch_remainder(tmp_path,monkeypatch,capsys):
    import eval.run_eval as cli
    monkeypatch.setattr(cli,'ROOT',ROOT)
    monkeypatch.setattr(cli,'EVAL_CACHE_DIR',ROOT/'.cache'/'eval-classifications')
    monkeypatch.setattr(cli,'RESULTS_DIR',tmp_path/'results')
    evalcache=cli.EVAL_CACHE_DIR;evalcache.mkdir(parents=True,exist_ok=True);(evalcache/'test').write_text('x')
    assert cli.main(['--provider','fake','--dataset',str(DATASET),'--batch-size','24','--clear-cache','--no-cache'])==0
    output=json.loads(capsys.readouterr().out)
    report=json.loads((Path(output['result_dir'])/'report.json').read_text())
    assert report['configuration']['batch_size']==24
    assert report['rule_based']['latency']['batch_count']==5
    assert not evalcache.exists()


def test_invalid_cli_arguments():
    from eval.run_eval import parser
    with pytest.raises(SystemExit):parser().parse_args(['--runs','0'])
    with pytest.raises(SystemExit):parser().parse_args(['--batch-size','3'])
