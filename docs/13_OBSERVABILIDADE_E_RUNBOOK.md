# 13 — Observabilidade e Runbook

> Monitoramento completo da plataforma: logging estruturado, metricas de sistema e negocio,
> alertas automaticos e procedimentos de resposta a incidentes.

---

## 1. Logging

### 1.1 Formato Estruturado (JSON)

Todos os logs sao emitidos em formato JSON para facilitar busca e analise no CloudWatch.

```python
import structlog

logger = structlog.get_logger()

# Exemplo de log por task
logger.info(
    "task_completed",
    service="video-service",
    task="generate_video",
    script_id=42,
    video_id=1,
    duration_seconds=32.5,
    cost_usd=0.80,
    heygen_job_id="abc123",
    elapsed_ms=845000
)
```

### 1.2 Campos Padrao

Todo log inclui automaticamente:

| Campo | Descricao | Exemplo |
|---|---|---|
| `timestamp` | ISO 8601 UTC | `2025-03-15T12:30:45.123Z` |
| `level` | Nivel do log | `info`, `warning`, `error` |
| `service` | Nome do servico | `video-service` |
| `task` | Nome da task Celery | `generate_video` |
| `run_id` | ID do daily_run (se aplicavel) | `42` |
| `correlation_id` | UUID para rastrear fluxo end-to-end | `550e8400-e29b...` |
| `env` | Ambiente | `production`, `development` |

### 1.3 Log Groups (CloudWatch)

| Log Group | Fonte | Retention |
|---|---|---|
| `/aiafiliado/worker` | Celery worker (todas as tasks) | 30 dias |
| `/aiafiliado/beat` | Celery beat (scheduler) | 30 dias |
| `/aiafiliado/orchestrator` | Pipeline orchestrator | 30 dias |
| `/aiafiliado/errors` | Somente erros (filtrado) | 90 dias |

---

## 2. Metricas de Sistema

### 2.1 Infraestrutura (CloudWatch Metrics)

| Metrica | Fonte | Threshold de Alerta |
|---|---|---|
| CPU Utilization | ECS / EC2 | > 80% por 5 min |
| Memory Utilization | ECS / EC2 | > 85% por 5 min |
| RDS CPU | RDS CloudWatch | > 70% por 10 min |
| RDS Connections | RDS CloudWatch | > 80% do max |
| RDS Free Storage | RDS CloudWatch | < 2 GB |
| Redis Memory | ElastiCache | > 80% |
| Redis Evictions | ElastiCache | > 0 |

### 2.2 Celery (Custom Metrics)

| Metrica | Como Coletar | Threshold de Alerta |
|---|---|---|
| Tasks pendentes na fila | `celery inspect active` + `reserved` | > 50 tasks |
| Tasks falhadas/hora | Counter no worker | > 5/hora |
| Task execution time (p95) | Timer no worker | > 30 min (video), > 5 min (outros) |
| Worker alive | Heartbeat | Ausente por > 5 min |

```python
# Emitir metricas custom para CloudWatch
import boto3

cloudwatch = boto3.client('cloudwatch')

def emit_metric(name: str, value: float, unit: str = 'Count'):
    cloudwatch.put_metric_data(
        Namespace='AIAfiliado',
        MetricData=[{
            'MetricName': name,
            'Value': value,
            'Unit': unit
        }]
    )

# Exemplos
emit_metric('CeleryQueueSize', queue_size)
emit_metric('TasksFailedPerHour', failed_count)
emit_metric('DailyCostUSD', today_cost, 'None')
```

---

## 3. Metricas de Negocio

| Metrica | Calculo | Frequencia | Publicacao |
|---|---|---|---|
| Videos gerados/dia | `COUNT(videos WHERE created_at = today)` | Diario | CloudWatch custom metric |
| Publicacoes/dia | `COUNT(publications WHERE posted_at = today AND status = 'POSTED')` | Diario | CloudWatch custom metric |
| Taxa de sucesso | `POSTED / (POSTED + FAILED) * 100` | Diario | CloudWatch custom metric |
| Custo/dia (USD) | `SUM(videos.cost_usd WHERE created_at = today)` | Diario | CloudWatch custom metric |
| Vendas/dia | `SUM(latest metrics.sales)` | Diario | CloudWatch custom metric |
| Receita/dia (R$) | `SUM(latest metrics.revenue)` | Diario | CloudWatch custom metric |
| ROI acumulado | `(receita_total - custo_total) / custo_total` | Semanal | Log |
| Produtos ativos | `COUNT(products WHERE is_active = TRUE)` | Diario | CloudWatch custom metric |

---

## 4. Alertas (CloudWatch Alarms)

### 4.1 Alertas Criticos (acao imediata)

| Alerta | Condicao | Acao |
|---|---|---|
| **Pipeline falhou 3x consecutivas** | `daily_runs.status = 'failed'` por 3 dias | Investigar logs, verificar APIs externas |
| **Risk score HIGH** | `daily_runs.risk_level = 'HIGH'` | Pipeline ja se auto-pausou. Investigar causa (remocoes? strikes?) |
| **Ban detectado** | Strike count > 0 (via Account Health) | Pausar tudo. Seguir runbook de ban |
| **Worker morto** | Heartbeat ausente > 5 min | Restart automatico (ECS) ou manual |

### 4.2 Alertas de Atencao (investigar)

| Alerta | Condicao | Acao |
|---|---|---|
| **Custo diario > limite** | `daily_cost_usd > DAILY_BUDGET_USD` | Pipeline ja se auto-pausou. Verificar se houve pico de geracao |
| **Fila Celery > 50 tasks** | Queue size > 50 | Verificar se worker esta processando. Escalar se necessario |
| **HeyGen timeout** | 3 timeouts consecutivos | HeyGen pode estar com problemas. Verificar status page |
| **Taxa de rejeicao compliance > 30%** | Roteiros rejeitados / total > 30% | Revisar prompts LLM, verificar se tendencias sao adequadas |
| **Zero publicacoes em 24h** | Nenhum post em dia util | Verificar pipeline, Account Health Gate, APIs |

### 4.3 Configuracao CloudWatch

```python
# Exemplo: Alerta de pipeline falhando
{
    "AlarmName": "PipelineFailedConsecutive",
    "Namespace": "AIAfiliado",
    "MetricName": "PipelineStatus",
    "Statistic": "Sum",
    "Period": 86400,          # 24h
    "EvaluationPeriods": 3,   # 3 dias consecutivos
    "Threshold": 0,           # 0 = todas falharam
    "ComparisonOperator": "LessThanOrEqualToThreshold",
    "AlarmActions": ["arn:aws:sns:sa-east-1:...:aiafiliado-alerts"]
}
```

---

## 5. Runbook

### 5.1 Incidente: Ban ou Strike de Conta

**Severidade:** CRITICA

**Deteccao:** Account Health Gate detecta strike/remocao → risk_level = HIGH → alerta

**Procedimento:**

1. **Imediato (automatico):** Pipeline pausa automaticamente (HIGH risk)
2. **Verificar causa:**
   - Acessar TikTok Creator Dashboard manualmente
   - Identificar qual video foi removido e motivo
   - Verificar se ha strike/warning oficial
3. **Avaliar dano:**
   - Strike leve (community guidelines warning) → aguardar decay (3-5 dias)
   - Strike grave (repeated violation) → risco de ban permanente
4. **Correcao:**
   - Se video especifico foi removido: revisar blocklist, adicionar palavras
   - Se padrao de conteudo: revisar prompts LLM e compliance
   - Adicionar regra na blocklist para evitar recorrencia
5. **Recuperacao:**
   - Aguardar score decair para MEDIUM (~3-5 dias)
   - Re-ativar pipeline com 1 post/dia
   - Monitorar por 1 semana
   - Se sem incidentes → voltar para LOW

### 5.2 Incidente: HeyGen Fora do Ar

**Severidade:** ALTA

**Deteccao:** 3 timeouts ou erros 5xx consecutivos na HeyGen API

**Procedimento:**

1. **Automatico:** Tasks de video entram em fila de retry
2. **Verificar:**
   - Status page HeyGen: verificar se ha outage reportado
   - Testar API manualmente com curl/Postman
3. **Se outage confirmado:**
   - Ativar fila de espera — videos pendentes ficam em queue
   - Pipeline continua gerando roteiros (nao depende de HeyGen)
   - Retry automatico a cada 1 hora
4. **Se durar > 24h:**
   - Considerar pausar pipeline de novos roteiros tambem
   - Enviar alerta ao operador
5. **Recuperacao:**
   - Quando HeyGen voltar, processar fila pendente
   - Verificar se creditos foram consumidos durante falha

### 5.3 Incidente: Custo Explodiu

**Severidade:** MEDIA

**Deteccao:** `daily_cost_usd > DAILY_BUDGET_USD * 1.5` (50% acima do budget)

**Procedimento:**

1. **Automatico:** Pipeline pausou ao atingir budget diario
2. **Investigar causa:**
   - Muitos videos gerados? (produto vencedor escalou demais?)
   - LLM consumiu mais tokens que o esperado?
   - HeyGen cobrou acima do normal? (retry loop?)
3. **Correcoes:**
   - Ajustar `MAX_VIDEOS_PER_DAY` se necessario
   - Verificar se retry loop nao esta duplicando geracao
   - Ajustar `DAILY_BUDGET_USD` se necessario
4. **Prevencao:**
   - Revisar limites de geracao por produto
   - Adicionar log de custo acumulado em cada task

### 5.4 Incidente: Fila Celery Crescendo

**Severidade:** MEDIA

**Deteccao:** Queue size > 50 tasks por > 30 minutos

**Procedimento:**

1. **Verificar worker:**
   - Worker esta rodando? (`celery inspect ping`)
   - Worker esta processando? (`celery inspect active`)
2. **Se worker morto:**
   - ECS deve restartar automaticamente
   - Se nao, restart manual: `aws ecs update-service --force-new-deployment`
3. **Se worker lento:**
   - Verificar se HeyGen esta lento (tasks de video demoram)
   - Verificar CPU/memoria do container
4. **Se acumulo normal (pico):**
   - Aguardar processamento
   - Considerar aumentar concurrency do worker

---

## 6. Dashboard Operacional

### 6.1 Metricas para Visualizacao

Recomendacao: Grafana conectado ao CloudWatch ou dashboard CloudWatch nativo.

| Painel | Metricas |
|---|---|
| **Pipeline Status** | Runs por dia (sucesso/falha), risk_level |
| **Producao** | Videos gerados, publicados, taxa sucesso |
| **Custos** | Custo diario, custo acumulado, custo por video |
| **Performance** | Views, retencao 3s, vendas, receita |
| **Saude** | Risk score, remocoes, strikes |
| **Infra** | CPU, memoria, fila Celery, latencia |

---

## Documentos Relacionados

| Documento | Relacao |
|---|---|
| [01_PRD.md](01_PRD.md) | KPIs que precisam ser monitorados |
| [02_ARQUITETURA.md](02_ARQUITETURA.md) | Servicos a monitorar |
| [04_ACCOUNT_HEALTH_ANTI_BAN.md](04_ACCOUNT_HEALTH_ANTI_BAN.md) | Alertas de risk HIGH |
| [11_TRACKING_E_OTIMIZACAO.md](11_TRACKING_E_OTIMIZACAO.md) | Metricas de negocio |
| [12_INFRA_CLOUD_DEPLOY.md](12_INFRA_CLOUD_DEPLOY.md) | Stack AWS monitorada |
