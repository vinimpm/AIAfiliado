# 12 — Infraestrutura Cloud e Deploy

> Stack AWS completa, Docker Compose para desenvolvimento local, CI/CD via GitHub Actions,
> estimativa de custos e seguranca.

---

## 1. Stack AWS

### 1.1 Visao Geral

```mermaid
graph TB
    subgraph AWS Cloud
        EB[EventBridge<br>Scheduler Diario] --> ECS[ECS Fargate<br>Containers]
        ECS --> RDS[(RDS PostgreSQL)]
        ECS --> REDIS[(ElastiCache Redis)]
        ECS --> S3[(S3<br>Videos)]
        ECS --> SM[Secrets Manager<br>API Keys]
        ECS --> CW[CloudWatch<br>Logs + Alertas]
    end

    ECS -->|APIs externas| TIKTOK[TikTok API]
    ECS -->|APIs externas| INSTAGRAM[Instagram API]
    ECS -->|APIs externas| YOUTUBE[YouTube API]
    ECS -->|APIs externas| HEYGEN[HeyGen API]
    ECS -->|APIs externas| LLM[OpenAI/Claude API]
```

### 1.2 Servicos AWS Detalhados

| Servico | Uso | Configuracao |
|---|---|---|
| **ECS Fargate** | Containers da aplicacao (workers Celery + beat + orchestrator) | 2 tasks (worker + beat), 0.5 vCPU, 1GB RAM cada |
| **RDS PostgreSQL** | Banco de dados principal | db.t3.micro, 20GB SSD, single-AZ |
| **ElastiCache Redis** | Broker Celery + cache de tendencias | cache.t3.micro, 1 node |
| **S3** | Armazenamento de videos gerados | Bucket com lifecycle policy (30 dias) |
| **Secrets Manager** | API keys (HeyGen, TikTok, Instagram, YouTube, LLMs) | Rotacao automatica onde possivel |
| **CloudWatch** | Logs estruturados + metricas + alarmes | Log groups por servico, retention 30 dias |
| **EventBridge** | Scheduler — trigger diario do pipeline | Cron: `0 9 * * ? *` (9h BRT) |
| **ECR** | Container registry | Repositorio privado para imagens Docker |

### 1.3 ECS Fargate — Tasks

| Task | Containers | vCPU | RAM | Funcao |
|---|---|---|---|---|
| `worker` | celery-worker | 0.5 | 1 GB | Executa tasks Celery (trend, product, script, video, publish, analytics) |
| `beat` | celery-beat | 0.25 | 0.5 GB | Scheduler de tasks periodicas (metrics collection a cada 6h) |

**Alternativa EC2 (custo menor):**

| Instancia | Custo | Uso |
|---|---|---|
| t3.small (2 vCPU, 2GB) | ~$15/mes | Worker + Beat + Redis (local) |
| t3.micro (1 vCPU, 1GB) | ~$8/mes | Minimo viavel (Fase 1) |

A escolha entre Fargate e EC2 depende da preferencia por simplicidade (Fargate) vs. custo (EC2).

### 1.4 RDS PostgreSQL

```
Engine: PostgreSQL 15+
Instance: db.t3.micro (Fase 1) → db.t3.small (Fase 3)
Storage: 20 GB gp3 SSD
Multi-AZ: Nao (single-AZ — custo)
Backup: Automatico, 7 dias retencao
Connection: VPC private subnet, security group restrito
```

### 1.5 ElastiCache Redis

```
Engine: Redis 7+
Node: cache.t3.micro (Fase 1)
Uso: Celery broker + result backend + cache de tendencias
Persistence: Nao (dados efemeros)
Connection: VPC private subnet
```

### 1.6 S3

```
Bucket: aiafiliado-videos-{env}
Lifecycle: Mover para S3 Infrequent Access apos 7 dias, deletar apos 30 dias
Encryption: AES-256 (server-side)
Access: Somente via IAM role do ECS
Pre-signed URLs: Para upload do Instagram (TTL 1h)
```

### 1.7 EventBridge

```python
# Trigger diario do pipeline
{
    "schedule": "cron(0 12 * * ? *)",  # 12:00 UTC = 09:00 BRT
    "target": {
        "arn": "ecs-task-arn",
        "input": {"action": "trigger_daily_pipeline"}
    }
}
```

---

## 2. Docker Compose (Desenvolvimento Local)

```yaml
version: "3.8"

services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: aiafiliado
      POSTGRES_USER: aiafiliado
      POSTGRES_PASSWORD: localdev
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  worker:
    build: .
    command: celery -A app.celery worker --loglevel=info --concurrency=2
    environment:
      - DATABASE_URL=postgresql://aiafiliado:localdev@postgres:5432/aiafiliado
      - REDIS_URL=redis://redis:6379/0
      - ENV=development
    depends_on:
      - postgres
      - redis
    volumes:
      - ./src:/app/src

  beat:
    build: .
    command: celery -A app.celery beat --loglevel=info
    environment:
      - DATABASE_URL=postgresql://aiafiliado:localdev@postgres:5432/aiafiliado
      - REDIS_URL=redis://redis:6379/0
      - ENV=development
    depends_on:
      - postgres
      - redis

volumes:
  pgdata:
```

**Comandos locais:**
```bash
# Subir ambiente
docker compose up -d

# Ver logs
docker compose logs -f worker

# Rodar migrations
docker compose exec worker alembic upgrade head

# Trigger pipeline manual
docker compose exec worker python -m app.cli trigger_pipeline
```

---

## 3. CI/CD — GitHub Actions

### 3.1 Pipeline

```yaml
# .github/workflows/deploy.yml
name: CI/CD

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install ruff
      - run: ruff check .
      - run: ruff format --check .

  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_DB: test_aiafiliado
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        ports: ["5432:5432"]
      redis:
        image: redis:7
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: pytest tests/ -v --cov=src

  build-and-deploy:
    needs: [lint, test]
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: sa-east-1
      - uses: aws-actions/amazon-ecr-login@v2
      - run: |
          docker build -t aiafiliado .
          docker tag aiafiliado:latest $ECR_REGISTRY/aiafiliado:latest
          docker push $ECR_REGISTRY/aiafiliado:latest
      - run: |
          aws ecs update-service --cluster aiafiliado --service worker --force-new-deployment
          aws ecs update-service --cluster aiafiliado --service beat --force-new-deployment
```

### 3.2 Etapas

| Etapa | Trigger | Acao |
|---|---|---|
| Lint | Push + PR | `ruff check` + `ruff format` |
| Test | Push + PR | `pytest` com PostgreSQL + Redis locais |
| Build | Push main | Docker build + push para ECR |
| Deploy | Push main | Force new deployment no ECS |

---

## 4. Estimativa de Custo Mensal

### 4.1 Fase 1 (MVP — EC2)

| Item | Servico | Custo Estimado (USD) |
|---|---|---|
| Compute | EC2 t3.micro (ou Fargate) | $8-15 |
| Database | RDS db.t3.micro | $15 |
| Cache | ElastiCache cache.t3.micro | $12 |
| Storage | S3 (< 1GB/mes) | $1 |
| Secrets | Secrets Manager (5 secrets) | $2 |
| Logs | CloudWatch (30 dias) | $3 |
| Video | HeyGen Creator ($24/mes) | $24 |
| LLM | GPT-4o-mini (~60 roteiros/mes) | $3 |
| **Total** | | **~$70-75/mes (~R$350-400)** |

### 4.2 Fase 3 (Escala)

| Item | Servico | Custo Estimado (USD) |
|---|---|---|
| Compute | ECS Fargate (2 tasks) | $25 |
| Database | RDS db.t3.small | $25 |
| Cache | ElastiCache cache.t3.micro | $12 |
| Storage | S3 (< 5GB/mes) | $2 |
| Secrets | Secrets Manager | $2 |
| Logs | CloudWatch | $5 |
| Video | HeyGen Business ($60/mes) | $60 |
| LLM | GPT-4o-mini (~180 roteiros/mes) | $8 |
| **Total** | | **~$140/mes (~R$700)** |

---

## 5. Seguranca

### 5.1 IAM Roles

| Role | Permissoes | Atribuido a |
|---|---|---|
| `ecs-task-execution-role` | ECR pull, CloudWatch logs, Secrets Manager read | ECS tasks |
| `ecs-task-role` | S3 read/write (bucket especifico), Secrets Manager read | Containers |
| `ci-deploy-role` | ECR push, ECS update-service | GitHub Actions |

### 5.2 Security Groups

| SG | Inbound | Outbound |
|---|---|---|
| `sg-ecs` | Nenhum (somente outbound) | All traffic (APIs externas) |
| `sg-rds` | Porta 5432 de `sg-ecs` apenas | Nenhum |
| `sg-redis` | Porta 6379 de `sg-ecs` apenas | Nenhum |

### 5.3 Secrets

| Secret | Servico | Rotacao |
|---|---|---|
| `heygen-api-key` | HeyGen | Manual |
| `tiktok-oauth-tokens` | TikTok | Automatica (refresh token) |
| `instagram-token` | Instagram | A cada 60 dias (long-lived) |
| `youtube-oauth-tokens` | YouTube | Automatica (refresh token) |
| `openai-api-key` | OpenAI | Manual |
| `database-url` | RDS | Manual |
| `affiliate-keys` | Hotmart, Amazon, Shopee | Manual |

---

## Documentos Relacionados

| Documento | Relacao |
|---|---|
| [01_PRD.md](01_PRD.md) | RNF-005 a RNF-014 — requisitos de infra e seguranca |
| [02_ARQUITETURA.md](02_ARQUITETURA.md) | Servicos que rodam nesta infra |
| [13_OBSERVABILIDADE_E_RUNBOOK.md](13_OBSERVABILIDADE_E_RUNBOOK.md) | CloudWatch config detalhado |
| [14_ROADMAP_E_BACKLOG.md](14_ROADMAP_E_BACKLOG.md) | Evolucao de infra por fase |
