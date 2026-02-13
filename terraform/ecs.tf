# ===========================================================================
# ECS Cluster
# ===========================================================================

resource "aws_ecs_cluster" "main" {
  name = var.project_name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "${var.project_name}-cluster" }
}

# ===========================================================================
# CloudWatch Log Groups
# ===========================================================================

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/${var.project_name}/worker"
  retention_in_days = 30

  tags = { Name = "${var.project_name}-worker-logs" }
}

resource "aws_cloudwatch_log_group" "beat" {
  name              = "/${var.project_name}/beat"
  retention_in_days = 30

  tags = { Name = "${var.project_name}-beat-logs" }
}

resource "aws_cloudwatch_log_group" "orchestrator" {
  name              = "/${var.project_name}/orchestrator"
  retention_in_days = 30

  tags = { Name = "${var.project_name}-orchestrator-logs" }
}

resource "aws_cloudwatch_log_group" "errors" {
  name              = "/${var.project_name}/errors"
  retention_in_days = 90

  tags = { Name = "${var.project_name}-errors-logs" }
}

# ===========================================================================
# Task Definition — Worker
# ===========================================================================

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project_name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = "${aws_ecr_repository.main.repository_url}:latest"
      essential = true

      command = [
        "celery", "-A", "app.celery_app", "worker",
        "--loglevel=info", "--concurrency=2"
      ]

      portMappings = [
        {
          containerPort = 8080
          protocol      = "tcp"
        }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.worker.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "worker"
        }
      }

      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = aws_secretsmanager_secret.secrets["database-url"].arn
        },
        {
          name      = "HEYGEN_API_KEY"
          valueFrom = aws_secretsmanager_secret.secrets["heygen-api-key"].arn
        },
        {
          name      = "OPENAI_API_KEY"
          valueFrom = aws_secretsmanager_secret.secrets["openai-api-key"].arn
        }
      ]

      environment = [
        { name = "ENV", value = var.environment },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "S3_BUCKET_NAME", value = aws_s3_bucket.videos.id },
        { name = "REDIS_URL", value = "rediss://${aws_elasticache_cluster.main.cache_nodes[0].address}:6379/0" },
        { name = "REDIS_TLS", value = "true" },
        { name = "DATABASE_SSL", value = "true" },
      ]
    }
  ])

  tags = { Name = "${var.project_name}-worker-task" }
}

# ===========================================================================
# Task Definition — Beat
# ===========================================================================

resource "aws_ecs_task_definition" "beat" {
  family                   = "${var.project_name}-beat"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.beat_cpu
  memory                   = var.beat_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "beat"
      image     = "${aws_ecr_repository.main.repository_url}:latest"
      essential = true

      command = [
        "celery", "-A", "app.celery_app", "beat",
        "--loglevel=info"
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.beat.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "beat"
        }
      }

      environment = [
        { name = "ENV", value = var.environment },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "S3_BUCKET_NAME", value = aws_s3_bucket.videos.id },
        { name = "REDIS_URL", value = "rediss://${aws_elasticache_cluster.main.cache_nodes[0].address}:6379/0" },
        { name = "REDIS_TLS", value = "true" },
        { name = "DATABASE_SSL", value = "true" },
      ]
    }
  ])

  tags = { Name = "${var.project_name}-beat-task" }
}

# ===========================================================================
# ECS Services
# ===========================================================================

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs.id]
  }

  tags = { Name = "${var.project_name}-worker-service" }
}

resource "aws_ecs_service" "beat" {
  name            = "beat"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.beat.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs.id]
  }

  tags = { Name = "${var.project_name}-beat-service" }
}
