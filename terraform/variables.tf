variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "sa-east-1"
}

variable "environment" {
  description = "Environment name (production, staging)"
  type        = string
  default     = "production"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "aiafiliado"
}

# --- VPC ---
variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets"
  type        = list(string)
  default     = ["10.0.10.0/24", "10.0.11.0/24"]
}

variable "availability_zones" {
  description = "Availability zones"
  type        = list(string)
  default     = ["sa-east-1a", "sa-east-1b"]
}

# --- RDS ---
variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "db_allocated_storage" {
  description = "RDS storage in GB"
  type        = number
  default     = 20
}

variable "db_name" {
  description = "Database name"
  type        = string
  default     = "aiafiliado"
}

variable "db_username" {
  description = "Database master username"
  type        = string
  default     = "aiafiliado"
  sensitive   = true
}

variable "db_password" {
  description = "Database master password"
  type        = string
  sensitive   = true
}

# --- ElastiCache ---
variable "redis_node_type" {
  description = "ElastiCache Redis node type"
  type        = string
  default     = "cache.t3.micro"
}

# --- ECS ---
variable "worker_cpu" {
  description = "Worker task CPU units (1024 = 1 vCPU)"
  type        = number
  default     = 512
}

variable "worker_memory" {
  description = "Worker task memory in MB"
  type        = number
  default     = 1024
}

variable "beat_cpu" {
  description = "Beat task CPU units"
  type        = number
  default     = 256
}

variable "beat_memory" {
  description = "Beat task memory in MB"
  type        = number
  default     = 512
}

variable "dashboard_cpu" {
  description = "Dashboard task CPU units"
  type        = number
  default     = 256
}

variable "dashboard_memory" {
  description = "Dashboard task memory in MB"
  type        = number
  default     = 512
}

# --- Monitoring ---
variable "alert_email" {
  description = "Email address for CloudWatch alarm notifications"
  type        = string
}
