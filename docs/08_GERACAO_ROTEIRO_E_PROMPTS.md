# 08 — Geracao de Roteiro e Prompts

> Sistema de geracao de roteiros para videos curtos via LLM, com variantes A/B,
> anti-duplicacao e adaptacao por plataforma.

---

## 1. Estrutura do Roteiro

Todo roteiro segue a estrutura de 5 blocos otimizada para videos curtos (20-45s):

| Bloco | Duracao | Funcao | Exemplo |
|---|---|---|---|
| **Hook** | 0-3s | Prender atencao imediata | "Voce sabia que 90% das pessoas erram isso?" |
| **Problema** | 3-10s | Identificar a dor do viewer | "Seu cabelo ta ressecado e nada funciona?" |
| **Demonstracao** | 10-25s | Mostrar a solucao (produto) | "Olha o que esse serum faz em 5 minutos..." |
| **Prova** | 25-35s | Credibilidade (social proof, dados) | "Mais de 50 mil pessoas ja usaram e aprovaram" |
| **CTA** | 35-45s | Direcionar para o link | "Link na bio pra voce garantir o seu!" |

---

## 2. Prompt Base para LLM

### 2.1 Template Principal

```python
SCRIPT_GENERATION_PROMPT = """
Voce e um roteirista especializado em videos curtos virais para {platform}.
Crie um roteiro de {duration_range} segundos para promover o produto abaixo,
conectando-o a tendencia atual.

TENDENCIA: {trend_name}
CATEGORIA: {trend_category}
PRODUTO: {product_title}
PRECO: R${product_price}
PLATAFORMA: {platform}

ESTRUTURA OBRIGATORIA:
1. HOOK (0-3s): Uma frase impactante que prenda a atencao.
   - Use pergunta, fato surpreendente ou provocacao.
   - NAO use "Voce nao vai acreditar" ou cliches.
2. PROBLEMA (3-10s): Identifique uma dor real do publico.
3. DEMONSTRACAO (10-25s): Mostre como o produto resolve o problema.
   - Seja especifico e visual. Descreva o que o avatar faz/mostra.
4. PROVA (25-35s): Social proof ou dado que gere credibilidade.
   - NAO invente numeros. Use linguagem como "milhares de pessoas".
5. CTA (35-45s): {cta_instruction}

REGRAS:
- Linguagem: portugues brasileiro informal, tom conversacional
- NAO faca claims medicos, financeiros ou garantias absolutas
- NAO mencione precos diretamente (exceto se for ponto forte)
- Inclua 3-5 hashtags relevantes na caption
- O roteiro deve soar natural quando falado por avatar feminino

FORMATO DE SAIDA (JSON):
{{
    "hook": "texto do hook",
    "body": "texto completo do problema + demonstracao + prova",
    "cta": "texto do CTA",
    "caption": "descricao para a plataforma com hashtags",
    "estimated_duration_seconds": 30
}}
"""
```

### 2.2 CTA por Plataforma

```python
CTA_INSTRUCTIONS = {
    "tiktok": "Direcione para TikTok Shop ou link na bio. Use 'Corre pra garantir o seu!' ou similar.",
    "instagram": "Direcione para link na bio. Use 'Link na bio!' ou 'Corre la nos stories!'.",
    "youtube": "Direcione para link na descricao. Use 'Link na descricao!' ou 'Confira abaixo!'."
}
```

---

## 3. Variacoes A/B

### 3.1 Combinacoes

Para cada par tendencia/produto, gerar variantes A/B:

| Dimensao | Variante A | Variante B |
|---|---|---|
| Hook | Pergunta direta | Fato/estatistica |
| CTA | Urgencia suave | Social proof |
| Caption | Hashtags trending | Hashtags de nicho |

**Total de combinacoes possiveis:** 2 hooks x 2 CTAs x 2 captions = **8**

Na pratica, gerar **2 variantes** (A e B) por execucao para economia de custo LLM:

```python
VARIANT_A_PROMPT_MODIFIER = """
ESTILO DO HOOK: Faca uma pergunta direta e provocativa.
ESTILO DO CTA: Use urgencia suave ("Garanta o seu antes que acabe").
"""

VARIANT_B_PROMPT_MODIFIER = """
ESTILO DO HOOK: Comece com um fato surpreendente ou dado.
ESTILO DO CTA: Use social proof ("Milhares de pessoas ja garantiram").
"""
```

### 3.2 Avaliacao do Vencedor

Apos 48 horas de publicacao, o analytics-service compara as variantes:

```python
winner = max(variants, key=lambda v: v.metrics.clicks / max(v.metrics.views, 1))
# Variante com melhor CTR vence
```

O roteiro vencedor informa o estilo preferido para geracoes futuras.

---

## 4. Anti-Duplicacao

### 4.1 Hash SHA-256

```python
import hashlib
import re

def normalize_text(text: str) -> str:
    """Normaliza texto para comparacao."""
    text = text.lower().strip()
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s]', '', text)
    return text

def generate_hash(hook: str, body: str, cta: str) -> str:
    """Gera hash unico do roteiro."""
    normalized = normalize_text(f"{hook} {body} {cta}")
    return hashlib.sha256(normalized.encode()).hexdigest()
```

- Hash e salvo na coluna `scripts.hash` (UNIQUE constraint)
- Se hash ja existe no banco → roteiro rejeitado, gerar novo

### 4.2 Similaridade Coseno

```python
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

def check_similarity(new_script: str, recent_scripts: list[str], threshold: float = 0.85) -> bool:
    """Retorna True se o novo roteiro e similar demais a algum existente."""
    all_texts = recent_scripts + [new_script]
    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform(all_texts)

    # Comparar novo roteiro com cada existente
    new_vector = tfidf_matrix[-1]
    for i in range(len(recent_scripts)):
        sim = cosine_similarity(tfidf_matrix[i], new_vector)[0][0]
        if sim > threshold:
            return True  # Muito similar → rejeitar
    return False
```

- Threshold: **0.85** (configuravel)
- Comparar contra os **ultimos 10 roteiros** publicados
- Se similar → rejeitar e pedir ao LLM para gerar novamente com instrucao de variacao

---

## 5. Adaptacao por Plataforma

| Aspecto | TikTok | Instagram | YouTube |
|---|---|---|---|
| Tom | Muito informal, giriado | Informal, aspiracional | Informativo, tutorial |
| Duracao | 20-35s (ideal) | 25-40s | 30-45s |
| CTA | "TikTok Shop" ou "Bio" | "Link na bio" | "Link na descricao" |
| Hashtags | 3-5, trending | 5-10, mix trending + nicho | 3-5 como tags |
| Caption | Curta, emojis OK | Mais elaborada | Descricao detalhada |
| Formato | Direto, rapido | Visual, estetico | Explicativo |

---

## 6. Exemplos de Roteiros Gerados

### Exemplo 1: Serum Vitamina C (TikTok, tendencia #skincare)

**Variante A:**
```json
{
    "hook": "Sua pele ta apagada e sem vida? Olha isso!",
    "body": "Eu tava cansada de gastar dinheiro com skincare que nao funciona. Ate que descobri esse serum de vitamina C que ta bombando no TikTok. Olha a diferenca na minha pele em uma semana! A textura melhora, as manchinhas clareiam, e aquele brilho natural volta. Mais de 50 mil pessoas ja compraram e a nota e 4.8 estrelas.",
    "cta": "Corre la no TikTok Shop pra garantir o seu antes que esgote!",
    "caption": "Minha pele nunca esteve tao bonita! #skincare #vitaminac #tiktokmademebuyit #pelebonita #cuidadoscomapele",
    "estimated_duration_seconds": 32
}
```

**Variante B:**
```json
{
    "hook": "90% das mulheres nao sabem que vitamina C muda tudo na pele!",
    "body": "Sabe aquela sensacao de pele sem vida, com manchas que nao somem? Vitamina C e o ingrediente que mais tem evidencia pra clarear e dar glow. Esse serum aqui e o mais vendido do TikTok Shop e eu entendi o por que. Aplicando todo dia de manha, em 7 dias ja vi diferenca. E nao sou so eu, a avaliacao dele e quase 5 estrelas.",
    "cta": "Milhares de pessoas ja garantiram o delas. Link no TikTok Shop!",
    "caption": "O serum que mudou minha rotina de skincare! #skincare #vitamincserum #glowup #cuidadoscomapele #tiktokmademebuyit",
    "estimated_duration_seconds": 35
}
```

---

## 7. Configuracoes

```python
SCRIPT_CONFIG = {
    "LLM_MODEL": "gpt-4o-mini",          # ou "claude-haiku"
    "LLM_TEMPERATURE": 0.8,               # criatividade
    "LLM_MAX_TOKENS": 500,
    "VARIANTS_PER_PRODUCT": 2,             # A e B
    "SIMILARITY_THRESHOLD": 0.85,
    "RECENT_SCRIPTS_COMPARE": 10,          # comparar com ultimos N
    "MAX_RETRIES_ON_DUPLICATE": 3,         # tentativas se duplicado
    "DURATION_RANGE": (20, 45),            # segundos
}
```

---

## Documentos Relacionados

| Documento | Relacao |
|---|---|
| [01_PRD.md](01_PRD.md) | RF-009, RF-018 |
| [02_ARQUITETURA.md](02_ARQUITETURA.md) | script-service — contrato de task |
| [03_DADOS_E_SCHEMAS.md](03_DADOS_E_SCHEMAS.md) | Tabela `scripts` |
| [07_CONTEUDO_E_POLITICAS.md](07_CONTEUDO_E_POLITICAS.md) | Validacao de compliance apos geracao |
| [09_AVATAR_E_GERACAO_DE_VIDEO.md](09_AVATAR_E_GERACAO_DE_VIDEO.md) | Consome roteiros para gerar video |
| [11_TRACKING_E_OTIMIZACAO.md](11_TRACKING_E_OTIMIZACAO.md) | A/B testing — determinar variante vencedora |
