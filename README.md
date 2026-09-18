# promo-banner-ai

MVP determinístico para transformar campanhas de supermercado em banners PNG de **1080 × 1080**. Sem IA, banco ou filas. O template é inspirado na estrutura de `referencia.jpeg`, com identidade própria: amarelo promocional, faixa de validade, cards claros, preços em verde e rodapé da loja.

## Arquitetura

```text
JSON → Pydantic → Layout Planner → DesignSpec → Jinja2/HTML/CSS → Chromium → PNG
```

- `backend/app/models/`: modelos imutáveis, preços Decimal, contrato e DesignSpec.
- `backend/app/layout/`: grid, restrições, afinidade, score e busca determinística.
- `backend/app/assets/processor.py`: leitura segura de imagens locais, orientação EXIF, redução proporcional e normalização em PNG embutido.
- `backend/app/renderer/`: combinação do DesignSpec com dados originais e screenshot.
- `backend/app/templates/supermarket_12/`: HTML/CSS do primeiro template.
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

O objeto raiz possui `campaign` (`title`, `valid_until`, `brand`, `address`, `phone`, `instagram`) e `products`. `template` é opcional e aceita apenas `supermarket_12`.

Validações (HTTP **422**): exatamente 12 produtos, IDs únicos, nomes e unidades não vazios, categoria suportada, validade no formato dia/mês/ano e data real. Preço deve ser **string decimal positiva**, com no máximo duas casas decimais e oito dígitos totais. Números JSON, inclusive floats, são rejeitados. Não há arredondamento silencioso. Limites de texto: nome 70 caracteres, unidade 12 e demais campos comerciais 120. Campos desconhecidos são rejeitados.

Categorias: `acougue`, `frios`, `padaria`, `hortifruti`, `mercearia`, `bebidas`, `limpeza`, `higiene`. A categoria é fornecida pelo usuário; não há classificação automática.

## Layout Planner

Grid fixo de 4 colunas × 3 linhas. `slot = y * 4 + x`, de 0 a 11. Vizinhança horizontal e vertical usa distância Manhattan igual a 1; diagonais não contam.

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

Pares não listados são neutros. O score soma afinidades de vizinhos, dá bônus a featured nas posições fortes (principalmente os cantos superiores) e desconta seis pontos por componente desconectado adicional de cada categoria. Featured não aumenta o card nesta fase.

## Imagens e renderização

Substitua os placeholders por fotos reais dentro de `assets/`. Não há download nem geração por IA. Caminhos que escapem da raiz de assets, incluindo symlinks, são recusados. Arquivos ausentes, inválidos, maiores que 10 MB ou 20 megapixels geram warning e placeholder identificável, sem interromper o banner. SVG não é aceito como asset de produto; use PNG, JPEG ou outro formato raster suportado por Pillow.

Imagens têm `object-fit: contain`. Dados são escapados por Jinja2. Chromium não acessa recursos de rede: os assets e o CSS são embutidos. Preços usam elementos separados para moeda, inteiro, centavos e unidade. Textos longos têm ajuste de fonte para preservar o conteúdo. A captura espera fontes e imagens, usa viewport 1080 × 1080 e escala 1, sem `full_page`. Pillow confere as dimensões antes de publicar o PNG por renomeação atômica.

## Testes

```bash
python -m pytest backend/tests -q
# ou com Chromium incluído na imagem:
docker compose run --rm banner python -m pytest backend/tests -q
```

O teste de POST usa Chromium real e verifica um PNG real. Portanto instale Playwright antes de executar a suíte local. Os testes cobrem Decimal, formatação, validações, grid, distância, restrições, afinidade, determinismo, integridade do DesignSpec, dados comerciais, assets, escaping e endpoints.

## Limitações

- Apenas 12 produtos, 1080 × 1080 e um template. Imagens do exemplo são placeholders explícitos.
- Busca heurística melhora agrupamento, mas não garante score ótimo global.
- Um navegador por requisição: adequado ao MVP com baixo volume; sem fila ou limite distribuído de concorrência.
- Sem autenticação, armazenamento remoto, histórico, download por API ou limpeza automática de output. Uso inicial local/controlado.
- Limites de texto e preço protegem a área visual; conteúdos excepcionalmente longos ficam em fonte menor.
- Reprodutibilidade visual pressupõe os mesmos assets, fontes e versão do Chromium; use Docker para ambiente consistente.

## Roadmap — não implementado

**Fase 2:** classificação automática por IA, remoção automática de fundo, templates para 4, 6, 8 e 16 produtos.

**Fase 3:** OR-Tools, layouts assimétricos, produtos destacados ocupando dois slots. A separação entre grid, placements e renderer é o ponto de extensão; o contrato atual continua fixo em 12 slots.

**Fase 4:** LLM como diretor de arte, seleção automática de template, geração de headline e análise visual multimodal.

**Fase 5:** Google Sheets, ERP, armazenamento, histórico de campanhas, aprovação humana e publicação automática em redes sociais.
