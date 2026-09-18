# Classification Evaluation

Avaliador offline/reprodutível de `RuleBasedProductClassifier`, do provider de IA usado pela API (`LLMProductClassifier`) e do pipeline híbrido real. Ele mede ground truth; não escolhe um threshold por você. Métricas de `--provider fake` verificam a infraestrutura e **não representam a qualidade de IA**.

## Dataset e ground truth

`datasets/products.json` contém 120 registros balanceados: 15 por cada categoria e 40 por nível (`easy`, `medium`, `hard`). Há nomes simples, compostos, marcas e itens cuja classificação exige generalização além das palavras das regras. Cada categoria e nível têm ground truth escrito manualmente; `expected_subcategory` também é curado e validado em snake_case. IDs, campos não vazios, categorias, subcategorias e dificuldades são validados antes da execução. O hash SHA-256 do JSON bruto identifica a versão no relatório. Uma alteração de exemplos muda o hash; comparar métricas entre versões requer observar esse hash.

`rule_matched` é calculado em execução pelo classificador real com `confidence >= BANNER_CLASSIFICATION_MIN_CONFIDENCE`. `rule_strong` marca regras com confidence `>= STRONG_CONFIDENCE` (0,90), independentemente do threshold configurado. `rule_known` permanece como alias compatível de `rule_matched`; nenhum desses campos é anotado manualmente.

## Execução

Na raiz do repositório:

```bash
python -m eval.run_eval --provider fake --dataset eval/datasets/products.json --batch-size 12
python -m eval.run_eval --provider fake --batch-size 24 --runs 3 --no-cache
```

Provider real:

```bash
BANNER_AI_PROVIDER=openai \
BANNER_AI_MODEL="$MODEL_NAME" \
BANNER_AI_API_KEY="$BANNER_AI_API_KEY" \
python -m eval.run_eval --provider openai --batch-size 12
```

`BANNER_AI_API_KEY` vem do ambiente e nunca aparece em resultados ou logs. O provider real usa exatamente o `LLMProductClassifier`, endpoint, prompt, JSON Schema e `ProductClassification` de produção. O HTTP payload de classificação só leva `product_id`, `name`, `unit`. Se provider/modelo/chave não estiverem configurados, a parte AI fica `null`/`NOT_RUN`; regras, dataset e modos offline ainda podem ser avaliados. `real_provider_eval` diferencia `NOT_RUN` (sem tentativa, incluindo credenciais ausentes), `COMPLETED` (pelo menos uma chamada batch concluída) e `ATTEMPTED_FAILED` (houve chamada, todas falharam). Os contadores por chamada são `provider_call_count`, `provider_success_count` e `provider_failure_count`. Requests reais podem gerar custo. Os preços não são presumidos: custo só é calculado com uso real de tokens e as duas opções `--input-cost-per-million` e `--output-cost-per-million`.

Opções: `--provider fake|openai`; `--dataset PATH`; `--batch-size 1|4|8|12|24`; `--runs N`; `--mode all|rule|ai|hybrid`; `--no-cache`; `--clear-cache`; preços opcionais por milhão de tokens. Default: fake, todos os modos, batch 12, uma run. O último batch pode ser menor. Execuções recebem diretório novo, sem sobrescrever relatórios prévios.

## Modos

- **rule:** chama as regras reais item por item, sem IA.
- **ai:** envia todos os produtos ao `LLMProductClassifier` por batches; as regras apenas calculam `rule_known`.
- **hybrid:** chama `resolve_classifications` compartilhado com a API, antes do planner. Usa o mesmo cache/threshold/regras/fallback de produção e não simula uma segunda implementação.

Provider fake devolve sempre `mercearia` com confiança 0,80. Isso é uma fixture deliberadamente simples para exercitar batches, cache, métricas, erros e relatórios. Seus acertos não são evidência da qualidade de um modelo. O modo rule e o AI real podem ser avaliados contra ground truth sem confundir a fixture com uma inferência real.

## Métricas

Accuracy usa todo produto no denominador; produtos sem categoria são erros `unresolved`. Precision/recall/F1 são one-vs-rest por categoria; macro é a média não ponderada das oito categorias. A matriz tem oito categorias verdadeiras e nove colunas previstas, incluindo `unresolved`. O relatório inclui difficulty, exact match da subcategoria (somente registros que têm expected subcategory; unresolved conta como erro), rule-known/unknown, erros individuais e contagem por confidence bin.

Threshold tables mostram accepted/rejected/coverage/accuracy dos candidatos por thresholds fixos de 0,50 a 0,95. Não modificam a classificação usada para o banner nem recomendam um threshold. Confidence bins arredondam a faixa ao centésimo informado (`0,00–0,49`, etc.). AI rule-known e unknown são medidos à parte, com especial interesse em rule-unknown.

Latência é wall clock total do modo e por batch avaliado, incluindo overhead local. `evaluation_batch_count`/`evaluation_batch_latencies` contam processamento do evaluator, inclusive regra/cache; `provider_call_count` e `provider_latency_p50`, `provider_latency_p95`, `provider_average_latency` medem exclusivamente invocações reais do classificador externo/fake. Um lote resolvido por regra ou cache não conta como chamada de provider. `p50` e `p95` usam interpolação linear. Tokens só aparecem se o endpoint devolver usage válido. Custo é `null` quando tokens ou ambos os preços não estão disponíveis; nunca estimado a partir de suposições.

## Runs, cache e repetibilidade

`agreement_rate` é a fração de IDs cuja categoria prevista permaneceu igual em todas as runs; unresolved é um estado observável e também pode mudar entre runs. `category_flip_count` conta IDs com mais de um estado/categoria. Para `--runs > 1`, o evaluator força cache desativado, sem leituras ou escritas entre runs; inferências reais podem repetir chamadas e cobrar novamente. Com uma run, `--no-cache` desliga cache. Por default há cache evaluator próprio em `.cache/eval-classifications/<provider>/`; o cache operacional `.cache/classifications/` nunca é usado. `--clear-cache` apaga exclusivamente o diretório evaluator padrão antes da execução.

O cache é de classificações validadas e usa a mesma chave e conteúdo do cache de produção, mas em diretório isolado. Cache real pode afetar uma avaliação de uma run: o campo `source=cache` fica visível em `predictions.csv`. Para nova amostra real, use `--no-cache`; informe isso ao comparar preço e latência. Fake em múltiplas runs continua byte a byte determinístico.

## Arquivos de output

Cada run grava em `eval/results/<timestamp>-<provider>/`:

- `report.json`: dataset/hash, configuração, métricas dos três modos, subcategoria, confidence, regra conhecida, latência, usage, custo e repetibilidade.
- `report.md`: tabelas legíveis, matriz de confusão e observações factuais.
- `predictions.csv`: uma linha por produto/modo/run.
- `errors.json`: erros e unresolved, ordenados por confidence decrescente; confiança nula vem por último.
- `confusion-matrix.csv`: matriz tabular com `unresolved`.

O JSON é a fonte de métricas. As previsões em CSV preservam os candidatos de confiança abaixo do threshold como `unresolved` em `predicted_category`, mantendo confidence/source para análise. As métricas e faixas de threshold continuam usando o candidato original quando aplicável.

`report.json` registra `confusion_matrix_mode`; a matriz CSV e Markdown, assim como `errors.json`, resumem o modo primário (`hybrid`, depois `ai`, depois `rule`). `predictions.csv` contém todos os modos solicitados.

## Limitações

Ground truth foi escrito manualmente e representa esta amostra, não todos os SKUs/lojas/idiomas. Dificuldade também é rótulo humano. `rule_matched` mede cobertura sob o threshold atual; `rule_strong` mede regras de alta confiança. Confiança produzida por LLM pode não estar calibrada; bins e coverage medem comportamento da amostra, não garantia estatística. Datasets pequenos ou mudanças de dataset não devem ser comparados sem hash. Fake nunca deve ser usado para alegar precisão. Avaliação real requer configuração/modelo válidos e pode ter custo; a integração testada por HTTP mock confirma protocolo/schema, não acurácia do provider.
