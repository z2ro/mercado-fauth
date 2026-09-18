import asyncio
import logging
import time
from pathlib import Path

from backend.app.classification.cache import cache_key, read_cache, write_cache
from backend.app.classification.llm import LLMProductClassifier
from backend.app.classification.models import ProductClassification, ProductClassificationInput
from backend.app.classification.resolver import resolve_classifications
from backend.app.classification.rules import RuleBasedProductClassifier
from backend.app.config import ClassificationSettings

from .dataset import EvalProduct
from .fake_provider import FakeProductClassifier
from .metrics import (CATEGORIES, confidence_analysis, confusion_matrix, grouped_accuracy,
                      percentile, repeatability, rule_known_analysis, score_rows, subcategory_accuracy)

logger=logging.getLogger(__name__)


def chunks(items: tuple, size: int) -> list[tuple]:
    if size < 1:
        raise ValueError('batch-size deve ser positivo.')
    return [items[i:i+size] for i in range(0,len(items),size)]


async def rule_known_flags(dataset: tuple[EvalProduct,...], threshold: float) -> dict[str,bool]:
    classifier=RuleBasedProductClassifier()
    result={}
    for product in dataset:
        item=ProductClassificationInput(product_id=product.id,name=product.name,unit=product.unit)
        try:
            prediction=await classifier.classify(item)
            result[product.id]=prediction.confidence>=threshold
        except ValueError:
            result[product.id]=False
    return result


def base_row(product: EvalProduct, run: int, rule_known: bool) -> dict:
    return {'run':run,'product_id':product.id,'name':product.name,'unit':product.unit,'difficulty':product.difficulty,
            'rule_known':rule_known,'expected_category':product.expected_category.value,'predicted_category':None,'candidate_category':None,
            'expected_subcategory':product.expected_subcategory,'predicted_subcategory':None,'confidence':None,
            'source':'unresolved','correct':False,'resolved':False,'candidate_correct':False}


async def evaluate_rule(dataset, flags, batch_size, runs, threshold=.70):
    classifier=RuleBasedProductClassifier()
    per_run=[];latencies=[];total_start=time.perf_counter()
    for run in range(1,runs+1):
        rows=[]
        for batch in chunks(dataset,batch_size):
            started=time.perf_counter()
            for product in batch:
                try:
                    prediction=await classifier.classify(ProductClassificationInput(product_id=product.id,name=product.name,unit=product.unit))
                    rows.append(make_row(product,run,flags[product.id],prediction,'rule',threshold))
                except ValueError:
                    rows.append(base_row(product,run,flags[product.id]))
            latencies.append(time.perf_counter()-started)
        per_run.append(rows)
    return summarize_mode(per_run,latencies,time.perf_counter()-total_start)


def make_row(product,run,rule_known,prediction,source,threshold=.70):
    row=base_row(product,run,rule_known)
    row.update(predicted_category=prediction.category.value,candidate_category=prediction.category.value,
               predicted_subcategory=prediction.subcategory,confidence=prediction.confidence,source=source,
               candidate_correct=prediction.category.value==product.expected_category.value)
    row['resolved']=prediction.confidence>=threshold
    row['predicted_category']=prediction.category.value if row['resolved'] else None
    row['predicted_subcategory']=prediction.subcategory if row['resolved'] else None
    row['source']=source if row['resolved'] else 'unresolved'
    row['correct']=bool(row['resolved'] and row['candidate_correct'])
    return row


def summarize_mode(per_run,latencies,total_time):
    rows=[row for run in per_run for row in run]
    metric=score_rows(rows)
    metric['confusion_matrix']=confusion_matrix(rows)
    metric['difficulty_metrics']=grouped_accuracy(rows,'difficulty',('easy','medium','hard'))
    metric['rule_known_analysis']=rule_known_analysis(rows)
    metric['subcategory_exact_accuracy']=subcategory_accuracy(rows)
    metric['confidence_analysis']=confidence_analysis(rows)
    metric['repeatability']=repeatability(per_run)
    metric['latency']={'total_time':total_time,'average_time_per_product':total_time/len(rows) if rows else None,
                       'batch_count':len(latencies),'batch_latencies':latencies,'average_batch_latency':sum(latencies)/len(latencies) if latencies else None,
                       'p50':percentile(latencies,50),'p95':percentile(latencies,95)}
    metric['rows']=rows
    metric['runs']=per_run
    return metric


async def evaluate_ai(dataset, flags, batch_size, runs, settings, provider, use_cache):
    per_run=[];latencies=[];total_start=time.perf_counter();usage={'input_tokens':0,'output_tokens':0,'total_tokens':0};usage_available=False
    classifier=FakeProductClassifier() if provider=='fake' else LLMProductClassifier(settings)
    for run in range(1,runs+1):
        rows=[]
        for batch in chunks(dataset,batch_size):
            candidates={};pending=[]
            for product in batch:
                item=ProductClassificationInput(product_id=product.id,name=product.name,unit=product.unit)
                path=settings.cache_dir/f'{cache_key(item,settings)}.json'
                cached=read_cache(path,item) if use_cache else None
                if cached and cached.confidence>=settings.min_confidence:
                    candidates[product.id]=(cached,'cache')
                else:
                    pending.append((product,item,path))
            if pending:
                started=time.perf_counter()
                if hasattr(classifier,'last_usage'):
                    classifier.last_usage=None
                try:
                    result=await asyncio.wait_for(classifier.classify_batch(tuple(item for _,item,_ in pending)),timeout=settings.timeout_seconds)
                    checked=result.for_inputs(tuple(item for _,item,_ in pending))
                    for product,item,path in pending:
                        prediction=checked[product.id]
                        candidates[product.id]=(prediction,'ai')
                        if use_cache:write_cache(path,prediction,'ai')
                except Exception as exc:
                    logger.warning('evaluation_classifier_batch_failed error=%s',type(exc).__name__)
                latencies.append(time.perf_counter()-started)
                reported=getattr(classifier,'last_usage',None)
                if reported:
                    usage_available=True
                    for key in usage:usage[key]+=reported[key]
            for product in batch:
                row=base_row(product,run,flags[product.id])
                if product.id in candidates:
                    prediction,source=candidates[product.id]
                    rows.append(make_row(product,run,flags[product.id],prediction,source,settings.min_confidence))
                else:
                    rows.append(row)
        per_run.append(rows)
    out=summarize_mode(per_run,latencies,time.perf_counter()-total_start)
    out['usage']=usage if usage_available else None
    return out


async def evaluate_hybrid(dataset,flags,batch_size,runs,settings,provider,use_cache):
    if not use_cache:
        settings=settings.model_copy(update={'cache_dir':settings.cache_dir/'disabled'})
    per_run=[];latencies=[];total_start=time.perf_counter();classifier=FakeProductClassifier() if provider=='fake' else LLMProductClassifier(settings);usage={'input_tokens':0,'output_tokens':0,'total_tokens':0};usage_available=False
    for run in range(1,runs+1):
        rows=[]
        # Each run gets a clean hybrid cache so repeats measure classifier stability.
        run_settings=settings if use_cache else settings.model_copy(update={'cache_dir':settings.cache_dir/f'run-{run}'})
        for batch in chunks(dataset,batch_size):
            inputs=tuple(ProductClassificationInput(product_id=p.id,name=p.name,unit=p.unit) for p in batch)
            started=time.perf_counter()
            if hasattr(classifier,'last_usage'):
                classifier.last_usage=None
            try:
                results=await resolve_classifications(inputs,settings=run_settings,classifier=classifier,use_cache=use_cache)
            except Exception as exc:
                logger.warning('evaluation_hybrid_batch_failed error=%s',type(exc).__name__)
                results={}
            latencies.append(time.perf_counter()-started)
            reported=getattr(classifier,'last_usage',None)
            if reported:
                usage_available=True
                for key in usage:usage[key]+=reported[key]
            for product in batch:
                row=base_row(product,run,flags[product.id])
                if product.id in results:
                    prediction,source=results[product.id]
                    rows.append(make_row(product,run,flags[product.id],prediction,source,settings.min_confidence))
                else:
                    rows.append(row)
        per_run.append(rows)
    out=summarize_mode(per_run,latencies,time.perf_counter()-total_start)
    out['usage']=usage if usage_available else None
    return out


def public_mode(result):
    if result is None:return None
    return {key:value for key,value in result.items() if key not in ('rows','runs')}


def mode_observations(name,result):
    if result is None:return []
    rows=result['rows'];n_unresolved=result['unresolved_products']
    facts=[]
    if n_unresolved:facts.append(f'{name}: {n_unresolved} produtos ficaram unresolved.')
    errors=[r for r in rows if not r['correct']]
    if errors:
        counts={category:sum(r['expected_category']==category for r in errors) for category in CATEGORIES}
        category=max(counts,key=counts.get)
        facts.append(f"{name}: {counts[category]} de {len(errors)} erros tinham categoria esperada {category}.")
    return facts


def assemble_report(dataset,sha,flags,configuration,mode_results,real_provider_eval='NOT_RUN',cost_rates=None):
    flat={name:public_mode(mode_results.get(name)) for name in ('rule','ai','hybrid')}
    # The main confusion/errors files describe hybrid when available, otherwise AI, otherwise rule.
    primary=next((mode_results[n] for n in ('hybrid','ai','rule') if mode_results.get(n)),None)
    matrix=primary['confusion_matrix'] if primary else {}
    dataset_info={'path':str(configuration['dataset']),'sha256':sha,'total_products':len(dataset),
        'categories':{c:sum(p.expected_category.value==c for p in dataset) for c in CATEGORIES},
        'difficulty':{d:sum(p.difficulty==d for p in dataset) for d in ('easy','medium','hard')},
        'rule_known_count':sum(flags.values()),'rule_unknown_count':len(flags)-sum(flags.values())}
    ai=mode_results.get('ai')
    rule_known_ai=rule_known_analysis(ai['rows']) if ai else {'rule_known_count':dataset_info['rule_known_count'],'rule_unknown_count':dataset_info['rule_unknown_count'],'accuracy_rule_known':None,'accuracy_rule_unknown':None}
    usage_sources=[mode_results[n]['usage'] for n in ('ai','hybrid') if mode_results.get(n) and mode_results[n].get('usage')]
    usage=({key:sum(source[key] for source in usage_sources) for key in ('input_tokens','output_tokens','total_tokens')}
           if usage_sources else {'input_tokens':None,'output_tokens':None,'total_tokens':None})
    input_cost=cost_rates[0] if cost_rates else None;output_cost=cost_rates[1] if cost_rates else None
    estimated_cost=None if usage['input_tokens'] is None or input_cost is None or output_cost is None else (usage['input_tokens']*input_cost+usage['output_tokens']*output_cost)/1_000_000
    confidence=ai['confidence_analysis'] if ai else {'bins':[],'thresholds':[]}
    observations=[]
    for name,result in mode_results.items():observations+=mode_observations(name,result)
    if configuration['provider']=='fake':observations.append('Fake provider predicts one fixed category and is infrastructure-only; its metrics do not measure AI quality.')
    return {'dataset':dataset_info,'configuration':configuration,'rule_based':flat['rule'],'ai':flat['ai'],'hybrid':flat['hybrid'],
        'rule_known_analysis':{'rule_known_count':dataset_info['rule_known_count'],'rule_unknown_count':dataset_info['rule_unknown_count'],
            'ai_accuracy_rule_known':rule_known_ai['accuracy_rule_known'],'ai_accuracy_rule_unknown':rule_known_ai['accuracy_rule_unknown']},
        'confidence_analysis':confidence,'latency':{n:mode_results[n]['latency'] for n in mode_results},
        'usage':usage,'cost':{'input_cost_per_million':input_cost,'output_cost_per_million':output_cost,'estimated_cost':estimated_cost},
        'repeatability':{n:mode_results[n]['repeatability'] for n in mode_results},'confusion_matrix':matrix,
        'confusion_matrix_mode':next((n for n in ('hybrid','ai','rule') if mode_results.get(n)),None),'observations':observations,
        'real_provider_eval':real_provider_eval}
