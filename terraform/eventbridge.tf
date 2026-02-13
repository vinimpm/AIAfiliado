# ===========================================================================
# EventBridge Rule — Daily Pipeline Trigger (12:00 UTC = 09:00 BRT)
# ===========================================================================

resource "aws_cloudwatch_event_rule" "daily_pipeline" {
  name                = "${var.project_name}-daily-pipeline"
  description         = "Trigger daily pipeline at 12:00 UTC (09:00 BRT)"
  schedule_expression = "cron(0 12 * * ? *)"

  tags = { Name = "${var.project_name}-daily-pipeline" }
}

resource "aws_iam_role" "eventbridge_ecs" {
  name = "${var.project_name}-eventbridge-ecs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = { Service = "events.amazonaws.com" }
      }
    ]
  })

  tags = { Name = "${var.project_name}-eventbridge-ecs" }
}

resource "aws_iam_role_policy" "eventbridge_ecs" {
  name = "${var.project_name}-eventbridge-ecs-run"
  role = aws_iam_role.eventbridge_ecs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecs:RunTask"
        ]
        Resource = aws_ecs_task_definition.worker.arn
      },
      {
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = [
          aws_iam_role.ecs_task_execution.arn,
          aws_iam_role.ecs_task.arn
        ]
      }
    ]
  })
}

resource "aws_cloudwatch_event_target" "daily_pipeline" {
  rule     = aws_cloudwatch_event_rule.daily_pipeline.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge_ecs.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.worker.arn
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets         = aws_subnet.private[*].id
      security_groups = [aws_security_group.ecs.id]
    }
  }

  input = jsonencode({
    containerOverrides = [
      {
        name    = "worker"
        command = ["python", "-c", "from services.orchestrator import trigger_daily_pipeline; trigger_daily_pipeline()"]
      }
    ]
  })
}
