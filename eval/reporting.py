import csv
import json
from pathlib import Path

from .metrics import CATEGORIES, UNRESOLVED

CSV_FIELDS=('run','mode','product_id','name','unit','difficulty','rule_known','rule_matched','rule_strong','expected_category','predicted_category','expected_subcategory','predicted_subcategory','confidence','source','correct','resolved')


def _safe(value):
    return value.value if hasattr(value,'value') else value


def _csv_row(row,mode):
    return {key:_safe(row.get(key)) for key in (*CSV_FIELDS[:-1], 'resolved') if key!='mode'} | {'mode':mode}


def confusion_csv(matrix,path:Path):
    labels=(*CATEGORIES,UNRESOLVED)
    with path.open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(('expected',*labels))
        for expected in CATEGORIES:writer.writerow((expected,*[matrix[expected][predicted] for predicted in labels]))


def write_outputs(directory:Path,report:dict,errors:list[dict],rows_by_mode:dict[str,list[dict]]):
    directory.mkdir(parents=True,exist_ok=False)
    (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    with (directory/'predictions.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=CSV_FIELDS);writer.writeheader()
        for mode,rows in rows_by_mode.items():
            for row in rows:writer.writerow(_csv_row(row,mode))
    errors=sorted(errors,key=lambda row:(row['confidence'] is None,-(row['confidence'] or 0),row['product_id']))
    (directory/'errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    confusion_csv(report['confusion_matrix'],directory/'confusion-matrix.csv')
    (directory/'report.md').write_text(markdown_report(report,errors))


def markdown_report(report:dict,errors:list[dict])->str:
    lines=['# Classification Evaluation','','## Configuration', '',
           f"- Provider: `{report['configuration']['provider']}`",
           f"- Model: `{report['configuration']['model'] or 'unavailable'}`",
           f"- Batch size: {report['configuration']['batch_size']}",
           f"- Runs: {report['configuration']['runs']}",
           f"- Real provider status: `{report.get('real_provider_eval','NOT_RUN')}`",
           f"- Provider skip reason: `{report.get('provider_skip_reason') or 'none'}`",
           f"- Fake provider is infrastructure only: {report['configuration']['fake_is_not_quality_eval']}", '',
           '## Dataset','',f"- Products: {report['dataset']['total_products']}",
           f"- Rule matched/unmatched: {report['dataset'].get('rule_matched_count',report['dataset'].get('rule_known_count',0))}/{report['dataset'].get('rule_unmatched_count',report['dataset'].get('rule_unknown_count',0))}",
           f"- Rule strong/without strong: {report['dataset'].get('rule_strong_count',0)}/{report['dataset'].get('rule_without_strong_count',0)}", '']
    for title,key in [('Rule Based','rule_based'),('AI','ai'),('Hybrid','hybrid')]:
        lines += [f'## {title}','']
        mode=report[key]
        if mode is None:lines+=['Not run: real provider credentials/configuration unavailable.',''];continue
        lines += [f"- Accuracy: {mode['accuracy']:.4f}",f"- Macro F1: {mode['macro_f1']:.4f}",
                  f"- Resolved: {mode['resolved_products']}; unresolved: {mode['unresolved_products']}",'']
    lines += ['## Category Metrics','','| Mode | Category | Precision | Recall | F1 | Support |','|---|---|---:|---:|---:|---:|']
    for mode_key in ('rule_based','ai','hybrid'):
        mode=report[mode_key]
        if mode is None:continue
        for cat,m in mode['category_metrics'].items():lines.append(f"| {mode_key} | {cat} | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} | {m['support']} |")
    lines += ['','## Difficulty','','| Mode | Difficulty | Accuracy | Products |','|---|---|---:|---:|']
    for key in ('rule_based','ai','hybrid'):
        if report[key] is None:continue
        for difficulty,m in report[key]['difficulty_metrics'].items():lines.append(f"| {key} | {difficulty} | {m['accuracy']:.3f} | {m['total_products']} |")
    lines += ['', '## Rule Known vs Unknown','','| Mode | Matched | Unmatched | Strong | Without strong | Acc matched | Acc unmatched | Acc without strong |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for key in ('rule_based','ai','hybrid'):
        if report[key] is None:continue
        m=report[key]['rule_known_analysis'];lines.append(f"| {key} | {m['rule_matched_count']} | {m['rule_unmatched_count']} | {m['rule_strong_count']} | {m['without_strong_rule_count']} | {m['accuracy_rule_matched']:.3f} | {m['accuracy_rule_unmatched']:.3f} | {m['accuracy_without_strong_rule']:.3f} |")
    lines += ['', '## Confusion Matrix','',f"Primary mode: `{report['confusion_matrix_mode']}`",'', '| Expected \\ Predicted | '+' | '.join((*CATEGORIES,UNRESOLVED))+' |','|'+'---|'*(len(CATEGORIES)+2)]
    matrix=report['confusion_matrix']
    for expected in CATEGORIES:lines.append('| '+expected+' | '+' | '.join(str(matrix[expected][p]) for p in (*CATEGORIES,UNRESOLVED))+' |')
    lines += ['', '## Confidence Analysis','','### Confidence bins','','| Bin | Count | Correct | Incorrect | Accuracy |','|---|---:|---:|---:|---:|']
    ca=report['confidence_analysis']
    for b in ca['bins']:lines.append(f"| {b['range']} | {b['count']} | {b['correct']} | {b['incorrect']} | {b['accuracy']:.3f} |")
    lines += ['', '### Thresholds','','| Threshold | Accepted | Rejected | Coverage | Correct accepted | Accuracy accepted |','|---:|---:|---:|---:|---:|---:|']
    for t in ca['thresholds']:lines.append(f"| {t['threshold']:.2f} | {t['accepted']} | {t['rejected']} | {t['coverage']:.3f} | {t['correct_accepted']} | {t['accuracy_among_accepted']:.3f} |")
    lines += ['', '## Latency','']
    for key in ('rule_based','ai','hybrid'):
        if report[key] is not None:
            latency=report[key]['latency']
            lines.append(f"- {key}: {latency['total_time']:.3f}s total, {latency.get('evaluation_batch_count',latency['batch_count'])} evaluation batches; {latency['provider_call_count']} provider calls, provider p50 {latency['provider_latency_p50']}, p95 {latency['provider_latency_p95']}s.")
    lines += ['', '## Provider Calls','',json.dumps(report.get('provider_metrics',{}),ensure_ascii=False)]
    lines += ['', '## Provider Usage','',json.dumps(report['usage'],ensure_ascii=False), '', '## Errors','']
    lines.append(f'Total errors including unresolved: {len(errors)}.')
    for err in errors[:20]:lines.append(f"- {err['product_id']} ({err['difficulty']}): expected {err['expected']}, predicted {err['predicted'] or 'unresolved'} (candidate {err.get('candidate_category')}), confidence {err['confidence']}, source {err['source']}.")
    lines += ['', '## Repeated Runs','',json.dumps(report['repeatability'],ensure_ascii=False), '', '## Observations','']
    for fact in report['observations']:lines.append(f'- {fact}')
    return '\n'.join(lines)+'\n'
