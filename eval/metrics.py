from collections import Counter
from math import ceil

CATEGORIES = ('acougue', 'frios', 'padaria', 'hortifruti', 'mercearia', 'bebidas', 'limpeza', 'higiene')
UNRESOLVED = 'unresolved'
THRESHOLDS = (0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
CONFIDENCE_BINS = ((0.0, .49), (.50, .59), (.60, .69), (.70, .79), (.80, .89), (.90, 1.0))


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    if not 0 <= percent <= 100:
        raise ValueError('Percentil deve estar entre 0 e 100.')
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    low = int(position)
    high = min(ceil(position), len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def score_rows(rows: list[dict], categories=CATEGORIES) -> dict:
    total = len(rows)
    resolved = [r for r in rows if r['predicted_category'] is not None]
    correct = sum(r['predicted_category'] == r['expected_category'] for r in rows)
    per_category = {}
    for category in categories:
        tp = sum(r['expected_category'] == category and r['predicted_category'] == category for r in rows)
        fp = sum(r['expected_category'] != category and r['predicted_category'] == category for r in rows)
        support = sum(r['expected_category'] == category for r in rows)
        precision, recall = safe_div(tp, tp + fp), safe_div(tp, support)
        per_category[category] = {'precision': precision, 'recall': recall, 'f1': safe_div(2 * precision * recall, precision + recall), 'support': support}
    macro = {key: safe_div(sum(v[key] for v in per_category.values()), len(per_category)) for key in ('precision', 'recall', 'f1')}
    return {'total_products': total, 'resolved_products': len(resolved), 'unresolved_products': total-len(resolved),
            'accuracy': safe_div(correct, total), 'macro_precision': macro['precision'], 'macro_recall': macro['recall'],
            'macro_f1': macro['f1'], 'category_metrics': per_category}


def confusion_matrix(rows: list[dict], categories=CATEGORIES) -> dict[str, dict[str, int]]:
    labels = (*categories, UNRESOLVED)
    matrix = {expected: {predicted: 0 for predicted in labels} for expected in categories}
    for row in rows:
        matrix[row['expected_category']][row['predicted_category'] or UNRESOLVED] += 1
    return matrix


def confidence_analysis(rows: list[dict]) -> dict:
    available = [r for r in rows if r['confidence'] is not None]
    bins = []
    for index, (low, high) in enumerate(CONFIDENCE_BINS):
        upper = CONFIDENCE_BINS[index + 1][0] if index + 1 < len(CONFIDENCE_BINS) else high
        selected = [r for r in available if low <= r['confidence'] < upper or index == len(CONFIDENCE_BINS)-1 and r['confidence'] == 1.0]
        correct = sum(r.get('candidate_correct', r['correct']) for r in selected)
        bins.append({'range': f'{low:.2f}-{high:.2f}', 'count': len(selected), 'correct': correct, 'incorrect': len(selected)-correct, 'accuracy': safe_div(correct,len(selected))})
    thresholds = []
    for threshold in THRESHOLDS:
        selected = [r for r in available if r['confidence'] >= threshold]
        accepted_correct = sum(r.get('candidate_correct', r['correct']) for r in selected)
        thresholds.append({'threshold':threshold, 'accepted':len(selected), 'rejected':len(rows)-len(selected), 'coverage':safe_div(len(selected),len(rows)), 'correct_accepted':accepted_correct, 'accuracy_among_accepted':safe_div(accepted_correct,len(selected))})
    return {'bins':bins,'thresholds':thresholds}


def grouped_accuracy(rows: list[dict], field: str, values: tuple[str, ...]) -> dict:
    out={}
    for value in values:
        selected=[r for r in rows if r[field]==value]
        out[value]={'total_products':len(selected),'accuracy':safe_div(sum(r['correct'] for r in selected),len(selected)),
                    'unresolved':sum(not r['resolved'] for r in selected)}
    return out


def rule_known_analysis(rows: list[dict]) -> dict:
    known=[r for r in rows if r['rule_known']]
    unknown=[r for r in rows if not r['rule_known']]
    return {'rule_known_count':len(known),'rule_unknown_count':len(unknown),
            'accuracy_rule_known':safe_div(sum(r['correct'] for r in known),len(known)),
            'accuracy_rule_unknown':safe_div(sum(r['correct'] for r in unknown),len(unknown))}


def subcategory_accuracy(rows: list[dict]) -> float | None:
    selected=[r for r in rows if r['expected_subcategory'] is not None]
    return safe_div(sum(r['expected_subcategory']==r['predicted_subcategory'] for r in selected),len(selected)) if selected else None


def repeatability(run_rows: list[list[dict]]) -> dict:
    if len(run_rows)<2:
        return {'runs':len(run_rows),'agreement_rate':None,'category_flip_count':0}
    by_id=[{r['product_id']:r['predicted_category'] for r in rows} for rows in run_rows]
    ids=sorted(by_id[0])
    flips=sum(len({run.get(product_id) for run in by_id})>1 for product_id in ids)
    return {'runs':len(run_rows),'agreement_rate':safe_div(len(ids)-flips,len(ids)),'category_flip_count':flips}
