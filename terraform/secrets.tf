# ===========================================================================
# AWS Secrets Manager
# ===========================================================================

locals {
  secrets = {
    "heygen-api-key"    = "CHANGE_ME"
    "openai-api-key"    = "CHANGE_ME"
    "tiktok-oauth"      = jsonencode({ client_key = "", client_secret = "", access_token = "" })
    "instagram-token"   = jsonencode({ access_token = "", user_id = "" })
    "youtube-oauth"     = jsonencode({ client_id = "", client_secret = "", refresh_token = "" })
    "hotmart-keys"      = jsonencode({ client_id = "", client_secret = "", access_token = "" })
    "amazon-keys"       = jsonencode({ access_key = "", secret_key = "", associate_tag = "" })
    "shopee-keys"       = jsonencode({ app_id = "", secret = "" })
    "database-url"      = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}/${var.db_name}"
  }
}

resource "aws_secretsmanager_secret" "secrets" {
  for_each = local.secrets

  name                    = "${var.project_name}/${each.key}"
  recovery_window_in_days = 7

  tags = { Name = "${var.project_name}-${each.key}" }
}

resource "aws_secretsmanager_secret_version" "secrets" {
  for_each = local.secrets

  secret_id     = aws_secretsmanager_secret.secrets[each.key].id
  secret_string = each.value
}
