variable "aws_region" {
  description = "AWS region — Milan for Italian regulatory proximity"
  type        = string
  default     = "eu-south-1"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "environment must be development, staging, or production"
  }
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.medium"
}

variable "db_password" {
  description = "TimescaleDB master password"
  type        = string
  sensitive   = true
}

variable "api_cpu" {
  description = "ECS task CPU units (1024 = 1 vCPU)"
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "ECS task memory in MB"
  type        = number
  default     = 1024
}

variable "ecr_repository_url" {
  description = "ECR repository URL for the API Docker image"
  type        = string
}

variable "image_tag" {
  description = "Docker image tag to deploy"
  type        = string
  default     = "latest"
}
