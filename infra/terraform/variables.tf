# =============================================================================
# Variables Terraform — VPP Italia
# Toutes les valeurs sensibles sont passées via terraform.tfvars (non commité)
# ou via des variables d'environnement TF_VAR_*
# =============================================================================

# -----------------------------------------------------------------------------
# Général
# -----------------------------------------------------------------------------

variable "aws_region" {
  description = "Région AWS — Milan pour proximité réglementaire et latence GME/Terna"
  type        = string
  default     = "eu-south-1"
}

variable "aws_account_id" {
  description = "ID du compte AWS (utilisé pour nommer le bucket S3 de façon unique)"
  type        = string
}

variable "environment" {
  description = "Environnement de déploiement"
  type        = string
  default     = "staging"
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "environment doit être : development, staging ou production."
  }
}

# -----------------------------------------------------------------------------
# Réseau
# -----------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR bloc du VPC dédié VPP"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidr" {
  description = "CIDR sous-réseau public (EC2 API)"
  type        = string
  default     = "10.0.1.0/24"
}

variable "private_subnet_cidr_a" {
  description = "CIDR sous-réseau privé A (RDS — AZ a)"
  type        = string
  default     = "10.0.10.0/24"
}

variable "private_subnet_cidr_b" {
  description = "CIDR sous-réseau privé B (RDS — AZ b, requis pour le subnet group)"
  type        = string
  default     = "10.0.11.0/24"
}

variable "admin_cidr" {
  description = "CIDR autorisé pour SSH sur l'EC2 (ex: votre IP fixe /32). ATTENTION : ne pas laisser 0.0.0.0/0 en production."
  type        = string
  default     = "0.0.0.0/0"
  validation {
    condition     = can(cidrnetmask(var.admin_cidr))
    error_message = "admin_cidr doit être un CIDR valide (ex: 203.0.113.0/32)."
  }
}

# -----------------------------------------------------------------------------
# EC2 — serveur API
# -----------------------------------------------------------------------------

variable "ec2_instance_type" {
  description = "Type d'instance EC2 pour l'API FastAPI"
  type        = string
  default     = "t3.micro"
}

variable "ec2_ami_id" {
  description = "AMI Ubuntu 22.04 LTS en eu-south-1. Vérifier la dernière version sur https://cloud-images.ubuntu.com/locator/ec2/"
  type        = string
  default     = "ami-0a6b545f62129c495"  # Ubuntu 22.04 LTS eu-south-1 (à mettre à jour)
}

variable "ec2_public_key" {
  description = "Clé SSH publique pour accès à l'instance EC2 (contenu du fichier .pub)"
  type        = string
  sensitive   = true
}

# -----------------------------------------------------------------------------
# RDS — TimescaleDB
# -----------------------------------------------------------------------------

variable "db_instance_class" {
  description = "Classe d'instance RDS PostgreSQL"
  type        = string
  default     = "db.t3.micro"
}

variable "db_password" {
  description = "Mot de passe maître PostgreSQL (utilisateur 'vpp')"
  type        = string
  sensitive   = true
  validation {
    condition     = length(var.db_password) >= 16
    error_message = "Le mot de passe DB doit faire au moins 16 caractères."
  }
}

# -----------------------------------------------------------------------------
# Tags & naming
# -----------------------------------------------------------------------------

variable "project_name" {
  description = "Nom du projet (utilisé dans les tags AWS)"
  type        = string
  default     = "vpp-italia"
}
