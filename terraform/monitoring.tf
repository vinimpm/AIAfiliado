# ===========================================================================
# SNS Topic for Alerts
# ===========================================================================

resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-alerts"

  tags = { Name = "${var.project_name}-alerts" }
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ===========================================================================
# ECS Alarms
# ===========================================================================

resource "aws_cloudwatch_metric_alarm" "ecs_cpu_high" {
  alarm_name          = "${var.project_name}-ecs-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "ECS CPU > 80% for 5 min"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.worker.name
  }

  tags = { Name = "${var.project_name}-ecs-cpu-high" }
}

resource "aws_cloudwatch_metric_alarm" "ecs_memory_high" {
  alarm_name          = "${var.project_name}-ecs-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = 300
  statistic           = "Average"
  threshold           = 85
  alarm_description   = "ECS Memory > 85% for 5 min"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.worker.name
  }

  tags = { Name = "${var.project_name}-ecs-memory-high" }
}

# ===========================================================================
# RDS Alarms
# ===========================================================================

resource "aws_cloudwatch_metric_alarm" "rds_cpu_high" {
  alarm_name          = "${var.project_name}-rds-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 70
  alarm_description   = "RDS CPU > 70% for 10 min"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  tags = { Name = "${var.project_name}-rds-cpu-high" }
}

resource "aws_cloudwatch_metric_alarm" "rds_storage_low" {
  alarm_name          = "${var.project_name}-rds-storage-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 2147483648 # 2 GB in bytes
  alarm_description   = "RDS Free Storage < 2 GB"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  tags = { Name = "${var.project_name}-rds-storage-low" }
}

resource "aws_cloudwatch_metric_alarm" "rds_connections_high" {
  alarm_name          = "${var.project_name}-rds-connections-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 50 # ~80% of db.t3.micro max (66)
  alarm_description   = "RDS Connections > 80% max"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  tags = { Name = "${var.project_name}-rds-connections-high" }
}

# ===========================================================================
# Custom Metric Alarms (namespace: AIAfiliado)
# ===========================================================================

resource "aws_cloudwatch_metric_alarm" "pipeline_failed_consecutive" {
  alarm_name          = "${var.project_name}-pipeline-failed-consecutive"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 3
  metric_name         = "PipelineFailed"
  namespace           = "AIAfiliado"
  period              = 86400 # 1 day
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "Pipeline failed 3 consecutive days"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "breaching"

  tags = { Name = "${var.project_name}-pipeline-failed" }
}

resource "aws_cloudwatch_metric_alarm" "daily_cost_over_budget" {
  alarm_name          = "${var.project_name}-daily-cost-over-budget"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DailyCostUSD"
  namespace           = "AIAfiliado"
  period              = 86400
  statistic           = "Maximum"
  threshold           = 5 # matches DAILY_BUDGET_USD default
  alarm_description   = "Daily cost exceeded budget"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.project_name}-cost-over-budget" }
}

resource "aws_cloudwatch_metric_alarm" "celery_queue_size" {
  alarm_name          = "${var.project_name}-celery-queue-large"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "CeleryQueueSize"
  namespace           = "AIAfiliado"
  period              = 300
  statistic           = "Maximum"
  threshold           = 50
  alarm_description   = "Celery queue size > 50"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.project_name}-celery-queue-large" }
}

resource "aws_cloudwatch_metric_alarm" "zero_publications_24h" {
  alarm_name          = "${var.project_name}-zero-publications-24h"
  comparison_operator = "LessThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "PublicationsPosted"
  namespace           = "AIAfiliado"
  period              = 86400
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Zero publications in 24h"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "breaching"

  tags = { Name = "${var.project_name}-zero-publications" }
}

# ===========================================================================
# ElastiCache Redis Alarms
# ===========================================================================

resource "aws_cloudwatch_metric_alarm" "redis_memory_high" {
  alarm_name          = "${var.project_name}-redis-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "Redis Memory > 80%"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    CacheClusterId = aws_elasticache_cluster.main.cluster_id
  }

  tags = { Name = "${var.project_name}-redis-memory-high" }
}

resource "aws_cloudwatch_metric_alarm" "redis_evictions" {
  alarm_name          = "${var.project_name}-redis-evictions"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Evictions"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Redis evictions detected"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    CacheClusterId = aws_elasticache_cluster.main.cluster_id
  }

  tags = { Name = "${var.project_name}-redis-evictions" }
}

# ===========================================================================
# Additional Custom Metric Alarms
# ===========================================================================

resource "aws_cloudwatch_metric_alarm" "heygen_timeout" {
  alarm_name          = "${var.project_name}-heygen-timeout"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "HeyGenTimeouts"
  namespace           = "AIAfiliado"
  period              = 3600
  statistic           = "Sum"
  threshold           = 3
  alarm_description   = "HeyGen API 3+ timeouts in 1 hour"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.project_name}-heygen-timeout" }
}

resource "aws_cloudwatch_metric_alarm" "compliance_rejection_high" {
  alarm_name          = "${var.project_name}-compliance-rejection-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ComplianceRejectionRate"
  namespace           = "AIAfiliado"
  period              = 86400
  statistic           = "Average"
  threshold           = 30
  unit                = "Percent"
  alarm_description   = "Compliance rejection rate > 30% in 24h"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.project_name}-compliance-rejection-high" }
}
