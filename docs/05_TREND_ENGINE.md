# 05 — Motor de Tendencias (Trend Engine)

> Sistema de coleta e ranqueamento de tendencias com potencial comercial,
> alimentando o pipeline com oportunidades de conteudo.

---

## 1. Objetivo

Identificar tendencias emergentes que:
- Estao em fase de crescimento (janela de oportunidade aberta)
- Sao replicaveis em formato de video curto
- Tem potencial de associacao com produtos vendaveis
- Ainda nao estao saturadas de conteudo

---

## 2. Fontes e Metodos de Coleta

### 2.1 TikTok Creative Center

| Atributo | Valor |
|---|---|
| **Metodo** | Web scraping da pagina de trending (Creative Center nao tem API publica estavel) |
| **URL base** | `https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/` |
| **Dados extraidos** | Hashtags trending, videos trending, musicas trending |
| **Campos por tendencia** | Nome, views totais, crescimento %, categoria, pais |
| **Rate limit** | Respeitar delay de 5-10s entre requests, rotacionar User-Agent |
| **Frequencia** | 1x/dia (inicio do pipeline) |
| **Fallback** | Se scraping falhar, usar cache do dia anterior |

**Dados coletados:**
```python
{
    "name": "#skincare",
    "views": 2500000000,
    "growth_7d": 0.45,      # +45% em 7 dias
    "category": "beauty",
    "country": "BR",
    "video_count": 150000
}
```

### 2.2 TikTok Shop (Produtos Trending)

| Atributo | Valor |
|---|---|
| **Metodo** | TikTok Shop Seller Center API / scraping de categorias populares |
| **Dados extraidos** | Produtos mais vendidos, categorias em alta, preco medio |
| **Campos por tendencia** | Nome do produto/categoria, vendas recentes, preco, ranking |
| **Rate limit** | Conforme documentacao da API |
| **Frequencia** | 1x/dia |

**Dados coletados:**
```python
{
    "name": "Serum Vitamina C",
    "category": "beauty",
    "sales_7d": 5000,
    "avg_price": 49.90,
    "source": "tiktok_shop"
}
```

### 2.3 Google Trends (pytrends)

| Atributo | Valor |
|---|---|
| **Metodo** | Biblioteca `pytrends` (Python wrapper para Google Trends) |
| **Dados extraidos** | Termos em alta, termos relacionados, interesse por regiao |
| **Campos** | Keyword, interesse (0-100), trending queries, related topics |
| **Rate limit** | Max 5 requests por minuto (Google pode bloquear) |
| **Frequencia** | 1x/dia |
| **Configuracao** | `geo='BR'`, `timeframe='now 7-d'` |

**Uso tipico:**
```python
from pytrends.request import TrendReq

pytrends = TrendReq(hl='pt-BR', tz=360)
pytrends.build_payload(['skincare'], timeframe='now 7-d', geo='BR')

# Interesse ao longo do tempo
interest = pytrends.interest_over_time()

# Termos relacionados em alta
related = pytrends.related_queries()

# Trending searches do dia
trending = pytrends.trending_searches(pn='brazil')
```

---

## 3. Algoritmo de Scoring

### 3.1 Formula Geral

```
trend_score = (
    crescimento    * 0.40 +
    repetibilidade * 0.20 +
    comprabilidade * 0.20 +
    saturacao_inv  * 0.20
)
```

### 3.2 Componentes Detalhados

#### Crescimento (40%)

Mede a velocidade de crescimento da tendencia.

```python
# Delta de crescimento: comparar interesse nas ultimas 24h vs. media de 7 dias
growth_24h = current_interest - avg_interest_7d
growth_rate = growth_24h / max(avg_interest_7d, 1)

if growth_rate >= 1.0:   crescimento = 100  # dobrou em 24h
elif growth_rate >= 0.5: crescimento = 80
elif growth_rate >= 0.2: crescimento = 60
elif growth_rate >= 0.1: crescimento = 40
elif growth_rate >= 0:   crescimento = 20
else:                    crescimento = 0    # em queda
```

#### Repetibilidade (20%)

Avalia se a tendencia gera formatos replicaveis em video curto.

```python
# Heuristicas:
# - Hashtag com formato definido (#GetReadyWithMe, #WhatIOrdered) → alta
# - Challenge com passos claros → alta
# - Evento unico/noticia → baixa

signals = []
if is_hashtag_challenge:    signals.append(80)
if has_template_format:     signals.append(90)
if is_recurring_topic:      signals.append(70)
if is_one_time_event:       signals.append(20)
if is_product_review:       signals.append(85)

repetibilidade = mean(signals) if signals else 50  # default medio
```

#### Comprabilidade (20%)

Verifica se existe produto associavel a tendencia.

```python
# Verificar se existe pelo menos 1 produto validado para esta tendencia
matching_products = query_product_service(trend_name, trend_category)

if len(matching_products) >= 3:  comprabilidade = 100
elif len(matching_products) >= 1: comprabilidade = 70
elif category_has_products:       comprabilidade = 40  # categoria tem produtos, mas nao match direto
else:                             comprabilidade = 0
```

#### Saturacao Inversa (20%)

Quanto menos conteudo existente, melhor a oportunidade.

```python
video_count = tendencia.video_count  # do TikTok Creative Center

if video_count < 1000:       saturacao_inv = 100  # nicho inexplorado
elif video_count < 10000:    saturacao_inv = 80
elif video_count < 50000:    saturacao_inv = 60
elif video_count < 200000:   saturacao_inv = 40
elif video_count < 1000000:  saturacao_inv = 20
else:                        saturacao_inv = 0    # muito saturado
```

---

## 4. Regras de Descarte

| Regra | Condicao | Acao |
|---|---|---|
| Score baixo | `trend_score < 60` | Descartar (status = `discarded`) |
| Queda consecutiva | Score caiu por 2 dias consecutivos | Descartar + expirar |
| Tendencia expirada | `expires_at < NOW()` | Status → `expired` |
| Categoria bloqueada | Tendencia em categoria proibida (ex: politica, saude sensivel) | Descartar |
| Duplicata | Tendencia ja processada nesta semana | Ignorar |

---

## 5. Cache e Frequencia de Atualizacao

| Item | Estrategia | TTL |
|---|---|---|
| Resultados do TikTok CC | Cache em Redis | 12 horas |
| Resultados do Google Trends | Cache em Redis | 6 horas |
| Tendencias processadas | PostgreSQL (tabela `trends`) | Permanente |
| Score calculado | Salvo no banco | Recalculado diariamente |

**Frequencia de execucao:**
- Pipeline principal: 1x/dia (inicio, apos Account Health Gate)
- Se Account Health Gate retorna MEDIUM: pular coleta de novas tendencias, usar apenas vencedores
- Se Account Health Gate retorna HIGH: nao executar

---

## 6. Output

Lista ranqueada de tendencias do dia, salva na tabela `trends`:

```python
[
    {
        "trend_id": 1,
        "run_id": 42,
        "name": "#VitaminaCSerum",
        "score": 82.0,
        "source": "tiktok_cc",
        "category": "beauty",
        "platform": "tiktok",
        "window_days": 5,
        "status": "active",
        "expires_at": "2025-03-20T00:00:00Z"
    },
    {
        "trend_id": 2,
        "run_id": 42,
        "name": "organizador de maquiagem",
        "score": 71.5,
        "source": "google_trends",
        "category": "beauty",
        "platform": "google",
        "window_days": 7,
        "status": "active",
        "expires_at": "2025-03-22T00:00:00Z"
    }
]
```

---

## Documentos Relacionados

| Documento | Relacao |
|---|---|
| [01_PRD.md](01_PRD.md) | RF-002, RF-003 |
| [02_ARQUITETURA.md](02_ARQUITETURA.md) | trend-service — contrato de task |
| [03_DADOS_E_SCHEMAS.md](03_DADOS_E_SCHEMAS.md) | Tabela `trends` |
| [06_PRODUTOS_E_VALIDACAO_COMERCIAL.md](06_PRODUTOS_E_VALIDACAO_COMERCIAL.md) | Consome tendencias para buscar produtos |
| [11_TRACKING_E_OTIMIZACAO.md](11_TRACKING_E_OTIMIZACAO.md) | Feedback de performance alimenta scoring |
