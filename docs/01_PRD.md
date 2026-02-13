# 01 — Product Requirements Document (PRD)

> Documento de requisitos do produto AIAfiliado — plataforma autonoma de geracao
> de videos curtos orientada por tendencias, com monetizacao via afiliacao.

---

## 1. Visao e Missao

**Visao:** Ser a maquina mais eficiente de conversao de tendencias virais em vendas
por afiliacao, operando de forma autonoma, segura e com custo previsivel.

**Missao:** Publicar diariamente videos curtos de alta retencao em TikTok, Instagram
e YouTube, conectando tendencias emergentes a produtos de afiliados, sem intervencao
manual e sem risco de banimento.

---

## 2. Persona

| Atributo | Descricao |
|---|---|
| **Quem** | Operador solo (o proprio desenvolvedor/dono) |
| **Uso** | Proprio, nao e SaaS |
| **Contas** | 1 conta por plataforma (TikTok, Instagram, YouTube) |
| **Objetivo** | Renda passiva via afiliacao com operacao automatizada |
| **Conhecimento** | Tecnico — capaz de configurar, deployar e monitorar |
| **Interacao** | Minima — verificar dashboard, ajustar configs quando necessario |

---

## 3. Objetivos SMART

| # | Objetivo | Especifico | Mensuravel | Meta |
|---|---|---|---|---|
| O1 | Publicar videos diariamente | Publicar em TikTok, Instagram e YouTube | Videos publicados/dia | >= 1 video/dia no TikTok (primario) |
| O2 | Alta retencao | Videos que prendem nos primeiros 3 segundos | Retencao 3s | > 70% |
| O3 | Converter em vendas | Gerar vendas reais via links de afiliado | Vendas/semana | >= 1 venda/semana apos Fase 2 |
| O4 | ROI positivo | Receita supera custos operacionais | ROI mensal | > 0% ate Fase 3 |
| O5 | Zero bans | Nenhuma conta banida permanentemente | Bans/mes | 0 |
| O6 | Custo previsivel | Custo operacional dentro do budget | Custo/mes | < R$500 (Fase 1), < R$1000 (Fase 3) |

---

## 4. KPIs Detalhados

### 4.1 KPIs de Conteudo

| KPI | Metrica | Meta | Frequencia |
|---|---|---|---|
| Retencao 3s | % de viewers que assistem > 3s | > 70% | Por video |
| Remocoes | % de videos removidos pela plataforma | < 1% | Mensal |
| Taxa de publicacao | Videos publicados vs. planejados | > 95% | Diario |
| Diversidade de conteudo | Similaridade coseno entre videos consecutivos | < 0.85 | Por video |

### 4.2 KPIs de Negocio

| KPI | Metrica | Meta | Frequencia |
|---|---|---|---|
| CTR (Click-Through Rate) | Clicks no link / Views | > 1% | Por video |
| Vendas por video | Vendas atribuidas ao video | >= 0.1 (media) | Semanal |
| Receita por video | R$ gerado por video | > R$5 (media) | Semanal |
| ROI mensal | (Receita - Custos) / Custos * 100 | > 0% | Mensal |
| Custo por video | Custo total / Videos produzidos | < R$15 | Mensal |

### 4.3 KPIs de Seguranca

| KPI | Metrica | Meta | Frequencia |
|---|---|---|---|
| Bans permanentes | Contas banidas permanentemente | 0 | Mensal |
| Dias em HIGH risk | Dias com risk_level = HIGH | < 2/mes | Mensal |
| Strikes | Avisos/strikes recebidos | < 1/mes | Mensal |
| Tempo de recuperacao | Dias de HIGH ate LOW | < 5 dias | Por incidente |

---

## 5. Requisitos Funcionais

| ID | Requisito | Prioridade | Fase |
|---|---|---|---|
| RF-001 | O sistema deve avaliar o health score da conta antes de qualquer acao diaria | MUST | 1 |
| RF-002 | O sistema deve coletar tendencias de TikTok Creative Center e Google Trends | MUST | 1 |
| RF-003 | O sistema deve pontuar tendencias com algoritmo multi-criterio (crescimento, repetibilidade, comprabilidade, saturacao) | MUST | 1 |
| RF-004 | O sistema deve validar produtos contra criterios comerciais (comissao, avaliacao, preco, categoria) | MUST | 1 |
| RF-005 | O sistema deve integrar com TikTok Shop Affiliate para obter links de afiliado | MUST | 2 |
| RF-006 | O sistema deve integrar com Hotmart/Monetizze para produtos digitais | SHOULD | 2 |
| RF-007 | O sistema deve integrar com Amazon Associates BR e Shopee Affiliate | SHOULD | 2 |
| RF-008 | O sistema deve validar roteiros contra politicas de conteudo via LLM | MUST | 2 |
| RF-009 | O sistema deve gerar roteiros com variantes A/B (hooks, CTAs, captions) | MUST | 1 |
| RF-010 | O sistema deve gerar videos via HeyGen API com avatar feminino realista | MUST | 1 |
| RF-011 | O sistema deve publicar videos via TikTok Content Posting API | MUST | 1 |
| RF-012 | O sistema deve publicar videos via Instagram Graph API (Reels) | SHOULD | 2 |
| RF-013 | O sistema deve publicar videos via YouTube Data API v3 (Shorts) | COULD | 3 |
| RF-014 | O sistema deve coletar metricas de performance (views, likes, retencao, vendas) | MUST | 1 |
| RF-015 | O sistema deve escalar produtos vencedores gerando novos videos automaticamente | MUST | 2 |
| RF-016 | O sistema deve pausar videos com baixa performance automaticamente | MUST | 2 |
| RF-017 | O sistema deve respeitar cooldowns e limites diarios definidos pelo Account Health Gate | MUST | 1 |
| RF-018 | O sistema deve aplicar anti-duplicacao via hash SHA-256 e similaridade coseno em roteiros | MUST | 1 |
| RF-019 | O sistema deve aplicar anti-duplicacao visual variando layout, legendas e B-roll | SHOULD | 2 |
| RF-020 | O sistema deve agendar publicacoes em horarios otimos por plataforma | SHOULD | 2 |
| RF-021 | O sistema deve reutilizar links de afiliado que performam bem em novos videos | MUST | 2 |
| RF-022 | O sistema deve aposentar produtos sem vendas apos 7 dias de inatividade | MUST | 2 |
| RF-023 | O sistema deve permitir mais de 1 video/dia para produtos vencedores (respeitando health gate) | SHOULD | 2 |

---

## 6. Requisitos Nao-Funcionais

### 6.1 Performance

| ID | Requisito | Meta |
|---|---|---|
| RNF-001 | Pipeline completo (trend-to-publish) deve completar em tempo habil | < 2 horas para 3 videos |
| RNF-002 | Coleta de tendencias | < 5 minutos |
| RNF-003 | Geracao de roteiro via LLM | < 30 segundos por roteiro |
| RNF-004 | Geracao de video via HeyGen | < 15 minutos por video |

### 6.2 Seguranca

| ID | Requisito |
|---|---|
| RNF-005 | API keys armazenadas em AWS Secrets Manager, nunca em codigo |
| RNF-006 | Tokens OAuth renovados automaticamente antes do vencimento |
| RNF-007 | Logs nao contem tokens, senhas ou dados sensiveis |
| RNF-008 | Acesso a infraestrutura via IAM roles com privilegio minimo |

### 6.3 Custo

| ID | Requisito | Meta |
|---|---|---|
| RNF-009 | Custo diario de infra AWS | < R$20/dia |
| RNF-010 | Custo por video (HeyGen + LLM) | < R$15/video |
| RNF-011 | Limite hard de gasto diario configuravel | Pausar pipeline se exceder |

### 6.4 Disponibilidade

| ID | Requisito | Meta |
|---|---|---|
| RNF-012 | Pipeline deve executar diariamente sem falha | > 95% uptime mensal |
| RNF-013 | Em caso de falha, retry automatico com backoff | Ate 3 tentativas |
| RNF-014 | Tolerancia a falha parcial — se um modulo falha, os demais continuam funcionais | Graceful degradation |

---

## 7. Restricoes Tecnicas e de Negocio

### Tecnicas
- **Linguagem:** Python 3.11+
- **Orquestracao:** Celery + Redis
- **Banco de dados:** PostgreSQL (AWS RDS)
- **Cloud:** AWS (ECS Fargate ou EC2, S3, Secrets Manager, CloudWatch, EventBridge)
- **Video:** HeyGen API (dependencia externa critica)
- **LLM:** OpenAI GPT-4o / GPT-4o-mini ou Anthropic Claude (para roteiros e compliance)
- **Publicacao:** APIs oficiais (TikTok Content Posting API, Instagram Graph API, YouTube Data API)

### Negocio
- **Orcamento mensal:** Limitado — operacao deve ser viavel com < R$1000/mes
- **Uma conta por plataforma:** Nao ha multi-conta ou rotacao de contas
- **Sem edicao manual:** Todo o pipeline e automatizado
- **Mercado:** Brasil (PT-BR), podendo expandir para outros idiomas no futuro

---

## 8. Fora de Escopo

| Item | Motivo |
|---|---|
| SaaS / multi-tenant | Uso exclusivamente proprio |
| Multiplas contas por plataforma | Complexidade e risco desnecessarios |
| Anuncios pagos (TikTok Ads, Meta Ads) | Foco em trafego organico |
| Edicao manual de video | Pipeline 100% automatizado |
| App mobile | Interface via dashboard web e configs |
| Marketplace proprio | Usa marketplaces existentes (TikTok Shop, Hotmart, Amazon, Shopee) |
| Vendas proprias | Apenas afiliacao — sem estoque ou logistica |

---

## 9. Premissas

| # | Premissa |
|---|---|
| P1 | As APIs oficiais (TikTok, Instagram, YouTube) permanecerao disponiveis e estaveis |
| P2 | HeyGen mantera API funcional com qualidade aceitavel de video |
| P3 | As plataformas de afiliacao (TikTok Shop, Hotmart, Amazon, Shopee) permanecerao acessiveis |
| P4 | O operador tem acesso aprovado a todas as APIs necessarias (credenciais, developer accounts) |
| P5 | O custo do HeyGen nao inviabiliza a operacao (plano Creator ou superior) |
| P6 | Tendencias sao detectaveis com antecedencia suficiente para produzir conteudo relevante |
| P7 | As LLMs (GPT-4o/Claude) mantem qualidade suficiente para roteiros e compliance |

---

## 10. Riscos

| # | Risco | Probabilidade | Impacto | Mitigacao |
|---|---|---|---|---|
| R1 | Banimento de conta | Media | Critico | Account Health Gate + limites conservadores |
| R2 | API do TikTok muda/quebra | Media | Alto | Versionamento de API + monitoramento |
| R3 | HeyGen indisponivel | Baixa | Alto | Fila de retry + fallback para pausa |
| R4 | Custo excede budget | Media | Medio | Limites hard de gasto + alertas |
| R5 | Tendencia esgota antes do video ficar pronto | Alta | Baixo | Pipeline rapido (< 2h) + priorizacao |
| R6 | Qualidade dos roteiros LLM cai | Baixa | Medio | A/B testing + monitoramento de metricas |
| R7 | Mudancas nas politicas de afiliacao | Baixa | Alto | Configuracao flexivel + monitoramento |
| R8 | Shadowban sem deteccao | Media | Alto | Monitoramento de alcance + queda brusca como sinal |

---

## Documentos Relacionados

| Documento | Relacao |
|---|---|
| [02_ARQUITETURA.md](02_ARQUITETURA.md) | Implementa os requisitos funcionais em servicos |
| [03_DADOS_E_SCHEMAS.md](03_DADOS_E_SCHEMAS.md) | Modela os dados necessarios para cada RF |
| [04_ACCOUNT_HEALTH_ANTI_BAN.md](04_ACCOUNT_HEALTH_ANTI_BAN.md) | Detalha RF-001, RF-017 |
| [11_TRACKING_E_OTIMIZACAO.md](11_TRACKING_E_OTIMIZACAO.md) | Detalha coleta de KPIs |
| [14_ROADMAP_E_BACKLOG.md](14_ROADMAP_E_BACKLOG.md) | Define fases de implementacao dos RFs |
