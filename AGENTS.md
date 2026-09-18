# Instruções para agentes

## Projeto

Mercado Fauth gera banners promocionais quadrados de 1080×1080 a partir de campanhas JSON. O backend é Python 3.12+, FastAPI, Pydantic v2, Jinja2 e Playwright. Não há banco de dados. Classificação por IA, Art Director e Visual QA multimodal são opcionais e ficam desativados por padrão.

## Arquitetura

Fluxo principal: `BannerRequest → classificação → direção visual opcional → Layout Planner → DesignSpec → renderer Jinja2/CSS → Chromium/Playwright → QA determinístico → PNG`.

- `backend/app/models/`: contrato de campanha/produto, categorias, `Decimal` e DesignSpecs.
- `backend/app/classification/`: classificador por regras, provider opcional, cache e resolução para `ResolvedProduct`.
- `backend/app/art_direction/`: contrato de direção visual, fallback determinístico, provider e cache.
- `backend/app/layout/`: seleção de template, placements, afinidades e restrições geométricas.
- `backend/app/design/system.py`: tokens visuais, perfis e escalas aprovadas.
- `backend/app/renderer/`: combinação do DesignSpec com dados originais, HTML, métricas DOM e captura.
- `backend/app/templates/<template>/`: HTML/CSS do template; não incluir lógica de planejamento ou alterar dados comerciais.
- `backend/app/assets/`: carregamento seguro, normalização e cache de imagens locais.
- `backend/app/visual_qa.py` e `backend/app/workflow.py`: validação visual e orquestração da geração.
- `backend/tests/`: testes unitários e integração com Playwright.

## Regras essenciais

- Preserve o comportamento público da API e payloads legados. O template padrão continua `supermarket_12`; escolha outros templates explicitamente.
- O MVP atual exige exatamente 12 produtos. `faith_reference_12` reutiliza a grade 4×3 legada; a ordem de entrada é mantida quando satisfaz as hard rules, caso contrário o planner reorganiza os itens.
- Nunca converta preços para `float`. O preço deve permanecer `Decimal` desde a validação até a renderização, com moeda, inteiro, centavos e unidade em elementos separados.
- `DesignSpec`/`ArtDirectionSpec` não devem conter nem modificar nome, preço, SKU, unidade, imagem ou outros dados comerciais. O renderer sempre consulta os modelos originais.
- O Layout Planner é o único responsável por coordenadas, placements, overlap, afinidade e hard rules. Não introduza coordenadas ou CSS arbitrários em saídas de IA.
- Produtos de departamentos incompatíveis não podem ficar adjacentes para resolver um layout impossível. Retorne o erro 422 existente; nunca relaxe as hard rules.
- Templates apresentam dados; não classificam produtos, escolhem placements nem reescrevem conteúdo comercial. Escape de HTML continua habilitado.
- Assets são locais. Preserve proteções contra path traversal e symlinks; não baixe nem acesse URLs de imagens durante renderização.
- Não habilite providers externos por padrão, não exponha credenciais e não faça chamadas reais em testes. Use fakes ou transports mockados.
- Evite banco de dados, filas e dependências novas sem necessidade concreta.

## Alterações e testes

Antes de editar, leia o módulo afetado, seus chamadores e testes próximos. Faça a menor alteração compatível e acrescente testes para mudanças de comportamento. Não remova ou enfraqueça testes existentes para fazer uma alteração passar.

Comandos locais:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q backend eval
git diff --check
```

Testes de renderização requerem Chromium instalado pelo Playwright:

```bash
.venv/bin/python -m playwright install chromium
```

Verificação em Docker:

```bash
docker compose build
docker compose up -d
docker compose exec -T banner python -m pytest
docker compose exec -T banner python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health").read().decode())'
```

Ao validar uma campanha gerada, confirme o status do endpoint, a aprovação do QA quando aplicável e as dimensões reais do PNG com Pillow. Não declare testes ou verificações que não foram executados.

## Arquivos gerados

- PNGs em `output/`, caches em `.cache/` e resultados de avaliação são artefatos locais; não os versione.
- Exemplos de payload ficam em `examples/`. Use somente assets sintéticos ou já licenciados no repositório.
- Atualize `README.md` quando contrato, comandos ou arquitetura visível ao usuário mudarem.
- Não faça commit, push, force push nem altere branch remota sem instrução explícita.
