import argparse
import asyncio
import json
import math
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from backend.app.config import ROOT, classification_settings

from .dataset import load_dataset
from .evaluator import (assemble_report,evaluate_ai,evaluate_hybrid,evaluate_rule,
                        public_mode,rule_known_flags)
from .reporting import write_outputs

EVAL_CACHE_DIR=ROOT/'.cache'/'eval-classifications'
DEFAULT_DATASET=ROOT/'eval'/'datasets'/'products.json'
RESULTS_DIR=ROOT/'eval'/'results'


def positive_int(value):
    try:number=int(value)
    except ValueError as exc:raise argparse.ArgumentTypeError('deve ser inteiro positivo') from exc
    if number<1:raise argparse.ArgumentTypeError('deve ser inteiro positivo')
    return number


def nonnegative_cost(value):
    try:number=float(value)
    except ValueError as exc:raise argparse.ArgumentTypeError('deve ser um número finito >= 0') from exc
    if not math.isfinite(number) or number<0:raise argparse.ArgumentTypeError('deve ser um número finito >= 0')
    return number


def parser():
    result=argparse.ArgumentParser(description='Avalia classificação de produtos contra ground truth.')
    result.add_argument('--provider',choices=('fake','openai'),default='fake')
    result.add_argument('--dataset',type=Path,default=DEFAULT_DATASET)
    result.add_argument('--batch-size',type=int,choices=(1,4,8,12,24),default=12)
    result.add_argument('--runs',type=positive_int,default=1)
    result.add_argument('--mode',choices=('all','rule','ai','hybrid'),default='all')
    result.add_argument('--no-cache',action='store_true')
    result.add_argument('--clear-cache',action='store_true')
    result.add_argument('--input-cost-per-million',type=nonnegative_cost)
    result.add_argument('--output-cost-per-million',type=nonnegative_cost)
    return result


def clear_eval_cache(cache_dir:Path):
    target=cache_dir.resolve()
    if target != (ROOT/'.cache'/'eval-classifications').resolve():
        raise ValueError('recusa limpar um diretório fora do cache evaluator')
    if target.exists():shutil.rmtree(target)


def result_directory(provider):
    stem=datetime.now().strftime('%Y%m%d-%H%M%S')+f'-{provider}'
    path=RESULTS_DIR/stem
    suffix=1
    while path.exists():
        suffix+=1;path=RESULTS_DIR/f'{stem}-{suffix:02d}'
    return path


def main(argv=None):
    args=parser().parse_args(argv)
    if args.input_cost_per_million is not None and args.output_cost_per_million is None or args.output_cost_per_million is not None and args.input_cost_per_million is None:
        parser().error('informe os dois preços por milhão de tokens para calcular custo')
    if args.clear_cache:
        clear_eval_cache(EVAL_CACHE_DIR)
    dataset,digest=load_dataset(args.dataset)
    settings=classification_settings()
    if args.provider=='fake':
        settings=settings.model_copy(update={'enabled':True,'provider':'fake','model':'fixed-baseline','cache_dir':EVAL_CACHE_DIR/'fake'})
    else:
        settings=settings.model_copy(update={'enabled':True,'provider':'openai','cache_dir':EVAL_CACHE_DIR/'openai'})
    effective_cache=not args.no_cache and args.runs==1
    flags=asyncio.run(rule_known_flags(dataset,settings.min_confidence))
    if args.provider=='openai' and (not settings.api_key.get_secret_value() or not settings.model or settings.provider not in ('','openai')):
        provider_available=False
    else:provider_available=True
    if args.provider=='openai':settings=settings.model_copy(update={'provider':'openai'})
    requested=('rule','ai','hybrid') if args.mode=='all' else (args.mode,)
    mode_results={}
    if 'rule' in requested:
        mode_results['rule']=asyncio.run(evaluate_rule(dataset,flags,args.batch_size,args.runs,settings.min_confidence))
    if 'ai' in requested and provider_available:
        ai_settings=settings.model_copy(update={'cache_dir':settings.cache_dir/'ai'})
        mode_results['ai']=asyncio.run(evaluate_ai(dataset,flags,args.batch_size,args.runs,ai_settings,args.provider,effective_cache))
    elif 'ai' in requested:
        mode_results['ai']=None
    if 'hybrid' in requested:
        hybrid_settings=settings.model_copy(update={'enabled':provider_available,'provider':'openai' if args.provider=='openai' else 'fake','cache_dir':settings.cache_dir/'hybrid'})
        mode_results['hybrid']=asyncio.run(evaluate_hybrid(dataset,flags,args.batch_size,args.runs,hybrid_settings,args.provider,effective_cache))
    real_status='RUN' if args.provider=='openai' and provider_available and any(n in mode_results for n in ('ai','hybrid')) else 'NOT_RUN'
    configuration={'provider':args.provider,'model':settings.model or ('fixed-baseline (not a real model)' if args.provider=='fake' else None),
        'batch_size':args.batch_size,'runs':args.runs,'mode':args.mode,'min_confidence':settings.min_confidence,
        'cache_enabled':effective_cache,'dataset':str(args.dataset.resolve()),
        'fake_is_not_quality_eval':args.provider=='fake'}
    costs=(args.input_cost_per_million,args.output_cost_per_million) if args.input_cost_per_million is not None else None
    report=assemble_report(dataset,digest,flags,configuration,mode_results,real_status,costs)
    primary=next((mode_results[n] for n in ('hybrid','ai','rule') if mode_results.get(n)),None)
    # Error records summarize the primary mode; predictions retain each requested mode and run.
    if primary:
        errors=[{'run':r['run'],'mode':next(n for n in ('hybrid','ai','rule') if mode_results.get(n) is primary),
                 'product_id':r['product_id'],'name':r['name'],'difficulty':r['difficulty'],'rule_known':r['rule_known'],
                 'expected':r['expected_category'],'predicted':r['predicted_category'],'candidate_category':r['candidate_category'],'confidence':r['confidence'],
                 'source':r['source'],'unresolved':not r['resolved']} for r in primary['rows'] if not r['correct']]
    output=result_directory(args.provider)
    write_outputs(output,report,errors,{name:result['rows'] for name,result in mode_results.items() if result})
    print(json.dumps({'result_dir':str(output),'provider':args.provider,'fake_is_not_quality_eval':args.provider=='fake',
        'real_provider_eval':real_status,'total_products':len(dataset),'rule_known':sum(flags.values()),'rule_unknown':len(flags)-sum(flags.values()),
        'modes':{n:public_mode(v) for n,v in mode_results.items()}},ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    sys.exit(main())
