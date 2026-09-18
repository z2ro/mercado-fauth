# promo-banner-ai

MVP determinístico para transformar campanhas de supermercado em banners PNG de **1080 × 1080**. Sem banco ou filas. Classificação opcional por LLM e remoção de fundo local; sem geração de imagens por IA. O template é inspirado na estrutura de `referencia.jpeg`, com identidade própria: amarelo promocional, faixa de validade, cards claros, preços em verde e rodapé da loja.

## Arquitetura

```text
JSON → Pydantic → Classificação → ResolvedProduct → Layout Planner → DesignSpec → Jinja2/HTML/CSS → Chromium → PNG
```

- `backend/app/models/`: modelos imutáveis, preços Decimal, contrato e DesignSpec.
- `backend/app/layout/`: grid, restrições, afinidade, score e busca determinística.
- `backend/app/design/system.py`: tokens de marca e escalas visuais compartilhados.
- `backend/app/assets/processor.py`: leitura segura de imagens locais, orientação EXIF, redução proporcional e normalização em PNG embutido.
- `backend/app/renderer/`: combinação do DesignSpec com dados originais e screenshot.
- `backend/app/templates/`: templates `supermarket_12`, `weekend_hero` e `price_attack`.
- `backend/app/main.py`: API síncrona FastAPI.
- `backend/tests/`: testes de domínio, planejamento, renderização e integração com Chromium real.

O DesignSpec contém apenas referências a produtos, slots, coordenadas e destaque. Nomes, preços, SKU e unidades vêm sempre dos modelos originais. Caixa alta é aplicada por CSS, sem alterar os nomes armazenados. Cada execução recebe um UUID para não sobrescrever arquivos anteriores; o planejamento é determinístico, o nome do arquivo não.

## Instalação local

Python **3.12+**, a partir da raiz deste repositório:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

A instalação das dependências do sistema do Chromium pode exigir privilégios administrativos no Linux. A documentação oficial explica a instalação e a [imagem Docker do Playwright](https://playwright.dev/python/docs/docker). A versão da imagem e do pacote Python está fixada em `1.58.0`.

## Docker

```bash
docker compose up --build
```

API: http://localhost:8000. OpenAPI: http://localhost:8000/docs.

Os diretórios `assets/` (somente leitura) e `output/` são montados no container; os PNGs permanecem no host. A imagem já inclui Chromium e bibliotecas do sistema. Para encerrar: `docker compose down`.

## Uso da API

```bash
curl -sS http://localhost:8000/health
curl --fail-with-body -sS -X POST http://localhost:8000/api/v1/banners \
  -H 'Content-Type: application/json' \
  --data-binary @examples/campaign.json
```

`GET /health` retorna `{"status":"ok"}`. `POST /api/v1/banners` retorna HTTP **201**:

```json
{
  "status": "created",
  "file": "output/<uuid>.png",
  "design": {
    "template": "supermarket_12",
    "width": 1080,
    "height": 1080,
    "products": [
      {"product_id": "p001", "slot": 0, "x": 0, "y": 0, "featured": true}
    ]
  }
}
```

O trecho de `products` acima é abreviado: o DesignSpec real sempre contém 12 posições. `file` é um caminho de arquivo, não uma URL de download. A pasta padrão é `output/`, podendo ser alterada com `BANNER_OUTPUT_DIR`; nesse caso, o nome retornado continua `output/<uuid>.png`, e o arquivo físico fica na pasta configurada. `BANNER_ASSET_DIR` altera a raiz dos assets.

## Entrada

O exemplo completo, executável, está em [`examples/campaign.json`](examples/campaign.json), com os 12 produtos solicitados e placeholders locais por categoria. Estrutura de um produto:

```json
{
  "id": "p001",
  "sku": "001",
  "name": "Paleta bovina",
  "price": "39.90",
  "unit": "KG",
  "category": "acougue",
  "subcategory": "carne_bovina",
  "image": "assets/products/acougue.png",
  "featured": true
}
```

O objeto raiz possui `campaign` (`title`, `valid_until`, `brand`, `address`, `phone`, `instagram`) e `products`. `template` é opcional e aceita `supermarket_12`, `weekend_hero` ou `price_attack`. Sem seleção explícita, produtos marcados como `featured` levam a `weekend_hero`; `visual_direction.emphasis="price"` sem destaques escolhe `price_attack`; demais campanhas mantêm o template legado.

Validações (HTTP **422**): exatamente 12 produtos, IDs únicos, nomes e unidades não vazios, categoria suportada, validade no formato dia/mês/ano e data real. Preço deve ser **string decimal positiva**, com no máximo duas casas decimais e oito dígitos totais. Números JSON, inclusive floats, são rejeitados. Não há arredondamento silencioso. Limites de texto: nome 70 caracteres, unidade 12 e demais campos comerciais 120. Campos desconhecidos são rejeitados.

Categorias: `acougue`, `frios`, `padaria`, `hortifruti`, `mercearia`, `bebidas`, `limpeza`, `higiene`. Categoria e subcategoria são opcionais. Quando presentes, têm precedência sobre a classificação automática.

## Layout Planner

O template legado usa grid fixo de 4 colunas × 3 linhas. `slot = y * 4 + x`, de 0 a 11. Vizinhança horizontal e vertical usa distância Manhattan igual a 1; diagonais não contam.

1. Ordena produtos por categoria, destaque e ID, independentemente da ordem do JSON.
2. Enumera as posições de limpeza: no máximo `C(12, 6) = 924` conjuntos.
3. Para cada conjunto, verifica se existem posições sem contato com limpeza para todos os produtos de açougue, hortifruti e padaria. Posiciona os demais nas posições restantes, com duas ordens de percurso.
4. Avalia candidatos e melhora os oito melhores por trocas de pares que aumentem estritamente o score e preservem as restrições.
5. Gera o DesignSpec sem duplicar dados comerciais.

A verificação de **viabilidade é completa para as três restrições atuais**: apenas a posição de limpeza determina os lugares permitidos para os departamentos sensíveis. Não há busca de 12! permutações. O score é heurístico; não garante ótimo global. Empates têm resolução estável.

Se a campanha for impossível (por exemplo, 11 itens de açougue e um de limpeza), retorna 422 com explicação. Nenhuma regra dura é relaxada.

### Regras e score

`layout/rules.py` centraliza incompatibilidades obrigatórias: limpeza nunca toca açougue, hortifruti ou padaria. `layout/affinity.py` centraliza todos os pesos suaves e afinidades simétricas:

| Categorias | Peso |
|---|---:|
| Mesma categoria | +8 |
| Açougue / frios | +3 |
| Açougue / mercearia | +1 |
| Açougue / bebidas | 0 |
| Hortifruti / mercearia | +2 |
| Hortifruti / padaria | +2 |
| Limpeza / higiene | +3 |
| Limpeza / bebidas | -2 |
| Frios / padaria | +2 |
| Mercearia / bebidas | +2 |
| Limpeza / açougue, hortifruti ou padaria | -10 e proibido |

Pares não listados são neutros. No template legado, o score soma afinidades de vizinhos, dá bônus a featured nas posições fortes (principalmente os cantos superiores) e desconta seis pontos por componente desconectado adicional de cada categoria.

## Layout Engine 2.0

`supermarket_12` e seu DesignSpec V1 continuam compatíveis. Os novos templates usam DesignSpec V2, um grid lógico retangular com placements (`x`, `y`, `w`, `h`), role (`hero`, `featured`, `standard`, `compact`), zona e escalas limitadas. O schema aceita apenas referências geométricas e metadados de apresentação; preço, nome, SKU, unidade e imagem continuam sendo lidos do produto original pelo renderer.

O planner escolhe um pattern estrutural, posiciona até um hero e até dois featured, evita que limpeza toque açougue, hortifruti ou padaria, e melhora o agrupamento com trocas locais determinísticas. Não permuta os 12 produtos exaustivamente. Placements retangulares não podem se sobrepor; adjacência considera bordas horizontais/verticais com trecho compartilhado, sem diagonais. Layouts impossíveis falham com erro em vez de relaxar uma regra.

`weekend_hero` reserva a metade superior esquerda para um hero grande e organiza as outras ofertas em blocos assimétricos. `price_attack` organiza 12 cards compactos em três faixas, com imagem e preço em composição horizontal. Defaults de cores, tipografia, espaçamento, raio, sombras e escalas por role estão centralizados em `backend/app/design/system.py` e são expostos aos templates como CSS variables.

Veja [`campaign-weekend-hero.json`](examples/campaign-weekend-hero.json) e [`campaign-price-attack.json`](examples/campaign-price-attack.json). Para escolher o hero, informe `template: "weekend_hero"` e `hero_products: ["p001"]`; `featured_products` aceita até dois IDs distintos. `visual_direction` recebe mood e ênfase validados, sem texto livre nem alteração de conteúdo comercial.

## Imagens e renderização

Substitua os placeholders por fotos reais dentro de `assets/`. Não há download de fotos nem geração por IA. Caminhos que escapem da raiz de assets, incluindo symlinks, são recusados. Arquivos ausentes, inválidos, maiores que 10 MB ou 20 megapixels geram warning e placeholder identificável, sem interromper o banner. SVG não é aceito como asset de produto; use PNG, JPEG ou outro formato raster suportado por Pillow.

Imagens têm `object-fit: contain`. Dados são escapados por Jinja2. Chromium não acessa recursos de rede: os assets e o CSS são embutidos. Preços usam elementos separados para moeda, inteiro, centavos e unidade. Textos longos têm ajuste de fonte para preservar o conteúdo. A captura espera fontes e imagens, usa viewport 1080 × 1080 e escala 1, sem `full_page`. Pillow confere as dimensões antes de publicar o PNG por renomeação atômica.

## Testes

```bash
python -m pytest backend/tests -q
# ou com Chromium incluído na imagem:
docker compose run --rm banner python -m pytest backend/tests -q
```

O teste de POST usa Chromium real e verifica um PNG real. Portanto instale Playwright antes de executar a suíte local. Os testes cobrem Decimal, formatação, validações, grid, distância, restrições, afinidade, determinismo, integridade do DesignSpec, dados comerciais, assets, escaping e endpoints.

## Limitações

- Apenas 12 produtos, 1080 × 1080 e três templates determinísticos; os assets do exemplo são sintéticos/placeholders, não fotos comerciais.
- Busca heurística melhora agrupamento, mas não garante score ótimo global.
- Um navegador por requisição: adequado ao MVP com baixo volume; sem fila ou limite distribuído de concorrência.
- Sem autenticação, armazenamento remoto, histórico, download por API ou limpeza automática de output. Uso inicial local/controlado.
- Limites de texto e preço protegem a área visual; conteúdos excepcionalmente longos ficam em fonte menor.
- Reprodutibilidade visual pressupõe os mesmos assets, fontes e versão do Chromium; use Docker para ambiente consistente.

## Roadmap

**Real assets + visual quality — implementado e testado:** preparação de imagens reais, remoção local opcional, crop, padding, cache por conteúdo, composição por aspect ratio e exemplos sintéticos.

**Classificação automática — implementada e testada offline:** regras determinísticas, cache, interface batch e provider LLM opcional com Structured Outputs. A integração externa foi validada com transporte HTTP simulado; não foi efetuada chamada paga.

**Layout Engine 2.0 — implementado e testado:** DesignSpec V2, roles e zones, placements retangulares, hero, templates `weekend_hero` e `price_attack`, tokens de marca e hard rules de adjacência retangular.

**Próxima fase:** AI Art Director com validação de template/hero/featured/direção visual e Visual QA. Esta execução não usa IA para escolher layout nem gerar conteúdo.

**Depois:** templates para 4, 6, 8 e 16 produtos; OR-Tools e placements com múltiplos slots; integrações externas e publicação.

Google Sheets, ERP, armazenamento, histórico de campanhas, aprovação humana e publicação automática em redes sociais seguem fora do MVP.


## Pipeline de fotos reais

`original → validação → EXIF → RGBA → remoção opcional → crop alpha → padding → resize proporcional → PNG → renderer`

Coloque a foto em `assets/products/` e use, por exemplo, `"image": "assets/products/arroz.jpg"`. São aceitos caminhos relativos à raiz de assets, com ou sem o prefixo `assets/`. URLs, caminhos absolutos e symlinks que escapem da raiz são rejeitados. Os limites continuam 10 MB e 20 megapixels. Formatos raster suportados por Pillow incluem PNG, JPEG, WebP, TIFF e BMP; em arquivos animados ou multipágina somente o primeiro frame é usado. SVG e conteúdo executável não são aceitos.

O crop usa apenas o canal alpha, sem tentar adivinhar o fundo. RGB opaco mantém o conteúdo inteiro quando a remoção está desligada. Após o crop, cada eixo recebe padding proporcional, com arredondamento para cima. O PNG RGBA tem lado máximo de 600 px, canvas ajustado ao conteúdo e proporção preservada; não há upscale no processamento nem ampliação além do tamanho natural no CSS. Assets completamente transparentes são tratados como inválidos e exibem placeholder.

Cards usam uma heurística genérica da imagem normalizada: proporção menor que 0,7 é vertical; maior que 1,5 é horizontal; demais usam a composição intermediária. A imagem permanece dentro do card e acima dos textos. Nenhuma regra depende de categoria, marca ou SKU. Preços continuam vindo do Decimal original.

### Remoção local de fundo

A interface `BackgroundRemover` tem implementações `NoOpBackgroundRemover` e `RembgBackgroundRemover`. A implementação real usa [rembg](https://github.com/danielgatis/rembg) 2.0.72, modelo U2NetP e ONNX Runtime em CPU. Não há API externa de inferência. O Docker provisiona o modelo de aproximadamente 4,6 MB durante o build; campanhas nunca baixam modelos ou imagens.

Para habilitar no Docker:

```bash
BANNER_REMOVE_BACKGROUND=true docker compose up --build -d
```

Para instalação local, depois de instalar `requirements.txt`, provisione uma vez (este comando precisa de internet):

```bash
python scripts/download_background_model.py
BANNER_REMOVE_BACKGROUND=true \
BANNER_BACKGROUND_MODEL="$PWD/.cache/models/u2netp.onnx" \
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

A sessão customizada do rembg abre somente o caminho local configurado. Se o modelo estiver ausente, falhar ou devolver imagem vazia/inválida, o pipeline registra `background_removal_failed` e continua com a imagem original corrigida. Esse resultado de fallback não é gravado como sucesso no cache, permitindo nova tentativa. Uma cópia é enviada ao remover para preservar o original mesmo em falhas parciais.

### Configuração

| Variável | Default | Validação / uso |
|---|---|---|
| `BANNER_REMOVE_BACKGROUND` | `false` | Booleano; use `true` ou `false` |
| `BANNER_ASSET_CACHE_DIR` | `.cache/assets` na raiz do projeto | Caminho local não vazio; no Compose, `/app/.cache/assets` em volume Docker persistente |
| `BANNER_ASSET_PADDING_RATIO` | `0.04` | Número finito entre 0 e 0,25, por eixo |
| `BANNER_BACKGROUND_MODEL` | `/opt/rembg/u2netp.onnx` | Modelo local U2NetP; ausência resulta em fallback |
| `OMP_NUM_THREADS` | `2` | Threads da inferência local |

Booleanos ou padding inválidos impedem a inicialização com erro de validação. Configurações, sessão e identidade do modelo são carregadas por processo: reinicie o serviço após trocar parâmetros ou modelo.

### Cache e logs

Arquivos `.cache/assets/<sha256>.png` são identificados pelos bytes originais, versão do pipeline, padding, limite de resize, opção de remoção, versão do remover e hash dos bytes do modelo. Não depende de nome ou timestamp da foto. Reutilizar a imagem evita decodificação do original, crop e inferência; somente o PNG em cache é carregado. A escrita usa arquivo temporário e renomeação atômica. Cache corrompido é reprocessado; falha de escrita gera warning e não impede renderização. Os originais nunca são sobrescritos.

Logs INFO: `asset_processing_started`, `asset_cache_hit`, `asset_cache_miss`, `background_removal_started`, `asset_normalized`. Falhas geram warning, incluindo `background_removal_failed`. Não se registram bytes nem base64. Para observar:

```bash
docker compose logs banner
```

O Compose usa o volume local `asset-cache`, separado do cache da instalação Python no host para evitar conflitos de permissões. Não há expiração automática do cache. Para recuperar espaço, remova os PNGs do diretório de cache com o serviço parado; a próxima campanha os recriará. Requisições simultâneas podem processar o mesmo cache miss mais de uma vez, sem corromper os arquivos.

### Exemplo com formatos variados

[`examples/campaign-real-assets.json`](examples/campaign-real-assets.json) mantém os mesmos dados comerciais do exemplo original e referencia quatro assets geométricos **sintéticos**, explicitamente identificados: vertical, horizontal, quadrado e conteúdo transparente com margem. Não são fotos comerciais. Para regenerá-los, execute `python scripts/create_synthetic_assets.py`.

```bash
curl --fail-with-body -sS -X POST http://localhost:8000/api/v1/banners \
  -H 'Content-Type: application/json' \
  --data-binary @examples/campaign-real-assets.json
```

A suíte usa fakes para remoção, cache isolado por teste e nenhum download de modelos. O teste de integração incorpora 12 imagens, verifica que Chromium as carregou e que ficam isoladas dos textos, preserva os dados comerciais e gera PNG 1080×1080 real. A inferência real é validada separadamente da suíte offline.

Limitações: U2NetP pode remover partes do produto ou deixar resíduos em fotos difíceis; avalie fotos representativas antes de ativar em produção. Transparência parcial é considerada conteúdo no crop. O canvas é limitado a 600×600, mas sua forma acompanha o produto; fotos muito pequenas permanecem pequenas para preservar qualidade. O pipeline é determinístico com o mesmo modelo, dependências e ambiente; não garante equivalência de pixels entre diferentes plataformas.


## Classificação automática de produtos

`category` e `subcategory` podem ser omitidas ou `null`. Categoria inválida continua sendo erro 422. A API resolve os produtos antes de executar o planner existente; `ResolvedProduct` exige categoria válida. O planner não recebe origem, confiança ou diagnóstico. A renderização, o DesignSpec e os dados comerciais continuam com os mesmos papéis.

Ordem de resolução:

1. Categoria manual tem precedência e nunca gera chamada de IA. Subcategoria manual é preservada literalmente. Se apenas subcategoria estiver ausente, uma regra compatível pode preenchê-la; caso contrário permanece `null`, sem impedir o banner.
2. Cache validado e com confiança suficiente.
3. Regra forte (confiança pelo menos 0,90 e acima do threshold configurado).
4. Uma chamada batch ao LLM para todos os produtos restantes, somente com IA habilitada.
5. Em erro, timeout, schema inválido ou baixa confiança, regra de fallback confiável.
6. Sem classificação segura: HTTP 422 com os IDs dos produtos não resolvidos. Nenhuma categoria padrão é inventada.

As regras estão em `backend/app/classification/rules.py`. Fazem comparação por palavras/frases normalizadas, sem acentos e sem distinção de maiúsculas. Frases específicas prevalecem sobre palavras contidas nelas: `água sanitária` é limpeza, `molho de tomate` é mercearia e `pão de queijo` é padaria. Matches conflitantes entre departamentos são recusados. Os pesos são heurísticos, não probabilidades calibradas. O vocabulário inicial tem limites: nomes desconhecidos ou ambíguos precisam de categoria manual ou IA; não há inferência silenciosa por default.

### Provider opcional e dados enviados

A interface `ProductClassifier` expõe `classify` e `classify_batch`. As implementações são `RuleBasedProductClassifier` e `LLMProductClassifier`. O provider inicial é `openai`, via Responses API e [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), usando `httpx` já instalado. Outros providers podem implementar a interface sem alterar o planner.

O prompt é versionado em `classification/llm.py`. O LLM recebe **somente product_id, name e unit** dos produtos pendentes. Não recebe SKU, preço, imagem, featured, dados da loja ou credenciais no prompt. Nomes são tratados como dados, nunca instruções. A saída aceita exclusivamente `product_id`, `category`, `subcategory` e `confidence`; campos extras são rejeitados. Não usamos justificativa livre (`reason`) no contrato do provider: o diagnóstico fica nos eventos e metadados, respeitando a restrição de saída exclusivamente classificatória.

Pydantic valida as oito categorias, subcategoria em snake_case sem acentos ou null e confiança numérica finita entre 0 e 1. O batch exige exatamente os IDs solicitados: duplicados, desconhecidos ou faltantes invalidam o batch completo. Respostas recusadas/incompletas também falham. Nunca se extrai categoria de texto livre. Há timeout HTTP e limite total de espera do batch, sem retries automáticos.

### Variáveis de classificação

| Variável | Default | Uso |
|---|---|---|
| `BANNER_AI_CLASSIFICATION_ENABLED` | `false` | Habilita uso do provider externo |
| `BANNER_AI_PROVIDER` | vazio | `openai` é o provider implementado |
| `BANNER_AI_MODEL` | vazio | Modelo da conta com suporte a Structured Outputs; não há modelo implícito |
| `BANNER_AI_API_KEY` | vazio | Segredo de execução, nunca versionar |
| `BANNER_CLASSIFICATION_MIN_CONFIDENCE` | `0.70` | Número finito de 0 a 1; aplicado também a regras e cache |
| `BANNER_AI_TIMEOUT_SECONDS` | `15` | Limite total do batch, maior que 0 e no máximo 120 segundos |
| `BANNER_CLASSIFICATION_CACHE_DIR` | `.cache/classifications` | Cache local; no Compose, volume persistente `classification-cache` |

Sem credencial, modelo ou provider suportado, o startup funciona. Produtos manuais e reconhecidos pelas regras continuam funcionando. Quando houver produto pendente, a tentativa de IA falha de forma controlada e segue para fallback/422. Configurações inválidas de threshold, timeout, booleano ou diretório vazio falham na inicialização. Reinicie o serviço depois de alterar a configuração.

Para habilitar, forneça as variáveis no ambiente e execute `docker compose up -d`. Mantenha a chave fora do Git. O serviço usa `store: false` no request ao provider; isso não substitui as políticas de retenção da conta. Sem IA habilitada, nenhuma chamada de classificação sai para a internet.

### Cache e observabilidade

O cache usa SHA-256 do nome e unidade normalizados, espaço reservado para marca futura, versão do classificador/regras/prompt e provider/modelo/configuração de habilitação. **Preço, SKU e ID não fazem parte da chave**: o mesmo produto pode reutilizar a categoria com outro preço ou ID. Ao ler, a identidade da classificação é associada ao ID atual, e o threshold é verificado novamente.

Cada JSON armazena categoria, subcategoria, confiança, identidade do classificador e versão. Escritas são atômicas; arquivos corrompidos são tratados como miss. Falha de escrita não impede o banner. Categorias manuais não alimentam o cache. Não há TTL; atualização de regras/prompt deve incrementar a respectiva versão. Cache de IA reduz variação em chamadas futuras, mas uma nova inferência externa não garante determinismo absoluto.

Eventos JSON nos logs: `classification_user_provided`, `classification_cache_hit`, `classification_cache_miss`, `classification_rule_match`, `classification_ai_started`, `classification_ai_completed`, `classification_low_confidence`, `classification_ai_failed`, `classification_fallback`, `classification_unresolved`. Erros do provider registram somente o tipo da exceção, nunca mensagens potencialmente contendo segredos.

A resposta existente ganha `classification`, indexado por ID:

```json
{"p001": {"category": "acougue", "subcategory": "carne_bovina", "source": "rule", "confidence": 0.95}}
```

`source` indica a origem da categoria (`user`, `cache`, `rule`, `ai`). Uma subcategoria manual preservada não altera a origem informada da categoria.

### Exemplo totalmente offline

[`examples/campaign-auto-classification.json`](examples/campaign-auto-classification.json) contém 12 produtos sem categoria nem subcategoria, incluindo pão francês. Funciona com os defaults, sem chave externa:

```bash
curl --fail-with-body -sS -X POST http://localhost:8000/api/v1/banners \
  -H 'Content-Type: application/json' \
  --data-binary @examples/campaign-auto-classification.json
```

Repita a chamada e observe `source: cache` e `classification_cache_hit`. A suíte usa `FakeProductClassifier`, `httpx.MockTransport` e caches temporários; não chama o provider real. Há testes de batch, timeout, falhas, threshold, preservação dos campos comerciais, integração com hard rules e screenshot Chromium 1080×1080.


## Classification Evaluation

O evaluator mede categorias, dificuldade, confidence, `rule_matched` (`>= min_confidence`) e `rule_strong` (`>= 0.90`), latência de avaliação e latência/counters do provider. `real_provider_eval` retorna `NOT_RUN`, `COMPLETED` ou `ATTEMPTED_FAILED`. Execute `python -m eval.run_eval --provider fake --dataset eval/datasets/products.json --batch-size 12`; o provider fake serve somente para testar a infraestrutura e **não mede qualidade de IA**. Para uma avaliação real, configure `BANNER_AI_PROVIDER=openai`, `BANNER_AI_MODEL` e `BANNER_AI_API_KEY` e use `--provider openai`; chamadas externas podem gerar custo. Relatórios ficam em `eval/results/<run-id>/`. Métricas, cache isolado e limitações estão em [`eval/README.md`](eval/README.md).
