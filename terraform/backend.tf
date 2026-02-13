terraform {
  backend "s3" {
    bucket         = "aiafiliado-terraform-state"
    key            = "infrastructure/terraform.tfstate"
    region         = "sa-east-1"
    encrypt        = true
    dynamodb_table = "aiafiliado-terraform-lock"
  }
}
