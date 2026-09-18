import asyncio
import logging
import time
from backend.app.classification.cache import cache_key, read_cache, write_cache
from backend.app.classification.llm import LLMProductClassifier
from backend.app.classification.models import ProductClassificationInput
from backend.app.classification.resolver import resolve_classifications
from backend.app.classification.rules import STRONG_CONFIDENCE, RuleBasedProductClassifier
from backend.app.config import ClassificationSettings

from .dataset import EvalProduct
from .fake_provider import FakeProductClassifier
from .metrics import (CATEGORIES, confidence_analysis, confusion_matrix, grouped_accuracy,
                      percentile, repeatability, rule_known_analysis, score_rows, subcategory_accuracy)

logger=logging.getLogger(__name__)


class MeasuredClassifier:
    """Count actual batch invocations, separately from evaluator batches."""
    def __init__(self, classifier):
        self.classifier=classifier
        self.provider_call_count=0;self.provider_success_count=0;self.provider_failure_count=0;self.provider_latencies=[]

    def __getattr__(self,name):return getattr(self.classifier,name)

    @property
    def last_usage(self):return getattr(self.classifier,'last_usage',None)

    @last_usage.setter
    def last_usage(self,value):
        if hasattr(self.classifier,'last_usage'):self.classifier.last_usage=value

    async def classify_batch(self,products):
        self.provider_call_count+=1;started=time.perf_counter()
        try:
            result=await self.classifier.classify_batch(products)
            result.for_inputs(products)
        except BaseException:
            self.provider_failure_count+=1;self.provider_latencies.append(time.perf_counter()-started);raise
        self.provider_success_count+=1;self.provider_latencies.append(time.perf_counter()-started)
        return result

    def metrics(self):
        return {'provider_call_count':self.provider_call_count,'provider_success_count':self.provider_success_count,
                'provider_failure_count':self.provider_failure_count,'provider_latency_p50':percentile(self.provider_latencies,50),
                'provider_latency_p95':percentile(self.provider_latencies,95),
                'provider_average_latency':sum(self.provider_latencies)/len(self.provider_latencies) if self.provider_latencies else None,
                'provider_latencies':self.provider_latencies}


def chunks(items: tuple, size: int) -> list[tuple]:
    if size < 1:
        raise ValueError('batch-size deve ser positivo.')
    return [items[i:i+size] for i in range(0,len(items),size)]


async def rule_classification_flags(dataset: tuple[EvalProduct,...], threshold: float) -> dict[str,dict[str,bool]]:
    classifier=RuleBasedProductClassifier()
    result={}
    for product in dataset:
        item=ProductClassificationInput(product_id=product.id,name=product.name,unit=product.unit)
        try:
            prediction=await classifier.classify(item)
            result[product.id]={'matched':prediction.confidence>=threshold,'strong':prediction.confidence>=STRONG_CONFIDENCE}
        except ValueError:
            result[product.id]={'matched':False,'strong':False}
    return result


async def rule_known_flags(dataset: tuple[EvalProduct,...], threshold: float) -> dict[str,bool]:
    """Compatibility wrapper: rule_known continues to mean rule_matched."""
    flags=await rule_classification_flags(dataset,threshold)
    return {product_id:flag['matched'] for product_id,flag in flags.items()}


def _rule_flags(flags):
    if isinstance(flags,dict):
        matched=flags.get('matched',flags.get('rule_matched',False))
        strong=flags.get('strong',flags.get('rule_strong',matched))
        return bool(matched),bool(strong)
    return bool(flags),bool(flags)


def base_row(product: EvalProduct, run: int, rule_flags) -> dict:
    rule_matched,rule_strong=_rule_flags(rule_flags)
    return {'run':run,'product_id':product.id,'name':product.name,'unit':product.unit,'difficulty':product.difficulty,
            'rule_known':rule_matched,'rule_matched':rule_matched,'rule_strong':rule_strong,
            'expected_category':product.expected_category.value,'predicted_category':None,'candidate_category':None,
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


def summarize_mode(per_run,latencies,total_time,measured=None):
    rows=[row for run in per_run for row in run]
    metric=score_rows(rows)
    metric['confusion_matrix']=confusion_matrix(rows)
    metric['difficulty_metrics']=grouped_accuracy(rows,'difficulty',('easy','medium','hard'))
    metric['rule_known_analysis']=rule_known_analysis(rows)
    metric['subcategory_exact_accuracy']=subcategory_accuracy(rows)
    metric['confidence_analysis']=confidence_analysis(rows)
    metric['repeatability']=repeatability(per_run)
    provider=measured.metrics() if measured else MeasuredClassifier(None).metrics()
    metric['latency']={'total_time':total_time,'average_time_per_product':total_time/len(rows) if rows else None,
                       'evaluation_batch_count':len(latencies),'evaluation_batch_latencies':latencies,
                       'average_evaluation_batch_latency':sum(latencies)/len(latencies) if latencies else None,
                       'batch_count':len(latencies),'batch_latencies':latencies,'average_batch_latency':sum(latencies)/len(latencies) if latencies else None,
                       'p50':percentile(latencies,50),'p95':percentile(latencies,95),**provider}
    metric['rows']=rows
    metric['runs']=per_run
    return metric


async def evaluate_ai(dataset, flags, batch_size, runs, settings, provider, use_cache):
    per_run=[];latencies=[];total_start=time.perf_counter();usage={'input_tokens':0,'output_tokens':0,'total_tokens':0};usage_available=False
    inner=FakeProductClassifier() if provider=='fake' else LLMProductClassifier(settings)
    classifier=MeasuredClassifier(inner)
    for run in range(1,runs+1):
        rows=[]
        for batch in chunks(dataset,batch_size):
            batch_started=time.perf_counter()
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
                reported=classifier.last_usage
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
            latencies.append(time.perf_counter()-batch_started)
        per_run.append(rows)
    out=summarize_mode(per_run,latencies,time.perf_counter()-total_start,classifier)
    out['usage']=usage if usage_available else None
    return out


async def evaluate_hybrid(dataset,flags,batch_size,runs,settings,provider,use_cache):
    if not use_cache:
        settings=settings.model_copy(update={'cache_dir':settings.cache_dir/'disabled'})
    per_run=[];latencies=[];total_start=time.perf_counter();inner=FakeProductClassifier() if provider=='fake' else LLMProductClassifier(settings);classifier=MeasuredClassifier(inner);usage={'input_tokens':0,'output_tokens':0,'total_tokens':0};usage_available=False
    for run in range(1,runs+1):
        rows=[]
        # Each run gets a clean hybrid cache so repeats measure classifier stability.
        run_settings=settings if use_cache else settings.model_copy(update={'cache_dir':settings.cache_dir/f'run-{run}'})
        for batch in chunks(dataset,batch_size):
            batch_started=time.perf_counter()
            inputs=tuple(ProductClassificationInput(product_id=p.id,name=p.name,unit=p.unit) for p in batch)
            classifier.last_usage=None
            try:
                results=await resolve_classifications(inputs,settings=run_settings,classifier=classifier,use_cache=use_cache)
            except Exception as exc:
                logger.warning('evaluation_hybrid_batch_failed error=%s',type(exc).__name__)
                results={}
            reported=classifier.last_usage
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
            latencies.append(time.perf_counter()-batch_started)
        per_run.append(rows)
    out=summarize_mode(per_run,latencies,time.perf_counter()-total_start,classifier)
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


def aggregate_provider_metrics(mode_results):
    names=('provider_call_count','provider_success_count','provider_failure_count')
    latencies=[v for result in mode_results.values() if result for v in result['latency']['provider_latencies']]
    return {**{name:sum(result['latency'][name] for result in mode_results.values() if result) for name in names},
            'provider_latency_p50':percentile(latencies,50),'provider_latency_p95':percentile(latencies,95),
            'provider_average_latency':sum(latencies)/len(latencies) if latencies else None,'provider_latencies':latencies}


def assemble_report(dataset,sha,flags,configuration,mode_results,real_provider_eval='NOT_RUN',cost_rates=None,provider_skip_reason=None):
    flat={name:public_mode(mode_results.get(name)) for name in ('rule','ai','hybrid')}
    # The main confusion/errors files describe hybrid when available, otherwise AI, otherwise rule.
    primary=next((mode_results[n] for n in ('hybrid','ai','rule') if mode_results.get(n)),None)
    matrix=primary['confusion_matrix'] if primary else {}
    matched_count=sum(_rule_flags(flag)[0] for flag in flags.values())
    strong_count=sum(_rule_flags(flag)[1] for flag in flags.values())
    dataset_info={'path':str(configuration['dataset']),'sha256':sha,'total_products':len(dataset),
        'categories':{c:sum(p.expected_category.value==c for p in dataset) for c in CATEGORIES},
        'difficulty':{d:sum(p.difficulty==d for p in dataset) for d in ('easy','medium','hard')},
        'rule_matched_count':matched_count,'rule_unmatched_count':len(flags)-matched_count,
        'rule_strong_count':strong_count,'rule_without_strong_count':len(flags)-strong_count,
        'rule_known_count':matched_count,'rule_unknown_count':len(flags)-matched_count}
    ai=mode_results.get('ai')
    rule_known_ai=rule_known_analysis(ai['rows']) if ai else None
    usage_sources=[mode_results[n]['usage'] for n in ('ai','hybrid') if mode_results.get(n) and mode_results[n].get('usage')]
    usage=({key:sum(source[key] for source in usage_sources) for key in ('input_tokens','output_tokens','total_tokens')}
           if usage_sources else {'input_tokens':None,'output_tokens':None,'total_tokens':None})
    input_cost=cost_rates[0] if cost_rates else None;output_cost=cost_rates[1] if cost_rates else None
    estimated_cost=None if usage['input_tokens'] is None or input_cost is None or output_cost is None else (usage['input_tokens']*input_cost+usage['output_tokens']*output_cost)/1_000_000
    confidence=ai['confidence_analysis'] if ai else {'bins':[],'thresholds':[]}
    observations=[]
    for name,result in mode_results.items():observations+=mode_observations(name,result)
    if configuration['provider']=='fake':observations.append('Fake provider predicts one fixed category and is infrastructure-only; its metrics do not measure AI quality.')
    provider_metrics=aggregate_provider_metrics(mode_results)
    return {'dataset':dataset_info,'configuration':configuration,'rule_based':flat['rule'],'ai':flat['ai'],'hybrid':flat['hybrid'],
        'rule_known_analysis':{'rule_matched_count':matched_count,'rule_unmatched_count':len(flags)-matched_count,
            'rule_strong_count':strong_count,'rule_without_strong_count':len(flags)-strong_count,
            'accuracy_rule_matched':rule_known_ai['accuracy_rule_matched'] if rule_known_ai else None,
            'accuracy_rule_unmatched':rule_known_ai['accuracy_rule_unmatched'] if rule_known_ai else None,
            'accuracy_rule_strong':rule_known_ai['accuracy_rule_strong'] if rule_known_ai else None,
            'accuracy_without_strong_rule':rule_known_ai['accuracy_without_strong_rule'] if rule_known_ai else None,
            'ai_accuracy_rule_known':rule_known_ai['accuracy_rule_known'] if rule_known_ai else None,
            'ai_accuracy_rule_unknown':rule_known_ai['accuracy_rule_unknown'] if rule_known_ai else None},
        'confidence_analysis':confidence,'latency':{n:mode_results[n]['latency'] for n in mode_results},
        'provider_metrics':provider_metrics,'provider_skip_reason':provider_skip_reason,
        'usage':usage,'cost':{'input_cost_per_million':input_cost,'output_cost_per_million':output_cost,'estimated_cost':estimated_cost},
        'repeatability':{n:mode_results[n]['repeatability'] for n in mode_results},'confusion_matrix':matrix,
        'confusion_matrix_mode':next((n for n in ('hybrid','ai','rule') if mode_results.get(n)),None),'observations':observations,
        'real_provider_eval':real_provider_eval}
