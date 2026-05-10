terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Backend S3 pour stocker le state Terraform en équipe.
  # À décommenter après avoir créé le bucket manuellement une première fois.
  # backend "s3" {
  #   bucket         = "vpp-italia-terraform-state"
  #   key            = "prod/terraform.tfstate"
  #   region         = "eu-south-1"
  #   encrypt        = true
  #   dynamodb_table = "terraform-state-lock"
  # }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "vpp-italia"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# =============================================================================
# VPC — réseau dédié VPP Italia
# =============================================================================

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "vpp-italia-${var.environment}" }
}

# Sous-réseau public — EC2 API (accès Internet entrant)
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true

  tags = { Name = "vpp-public-${var.environment}" }
}

# Sous-réseau privé — RDS (aucun accès Internet direct)
resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidr_a
  availability_zone = "${var.aws_region}a"

  tags = { Name = "vpp-private-a-${var.environment}" }
}

resource "aws_subnet" "private_b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidr_b
  availability_zone = "${var.aws_region}b"

  tags = { Name = "vpp-private-b-${var.environment}" }
}

# Internet Gateway — sortie Internet pour le sous-réseau public
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "vpp-igw-${var.environment}" }
}

# Table de routage publique
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "vpp-rt-public-${var.environment}" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# =============================================================================
# Security Groups
# =============================================================================

# SG API — port 8000 ouvert en entrée, SSH restreint à l'IP admin
resource "aws_security_group" "api" {
  name        = "vpp-api-${var.environment}"
  description = "FastAPI VPP — port 8000 public, SSH admin"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "API FastAPI"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSH admin"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "vpp-sg-api-${var.environment}" }
}

# SG RDS — port 5432 accessible uniquement depuis le SG de l'API
resource "aws_security_group" "rds" {
  name        = "vpp-rds-${var.environment}"
  description = "TimescaleDB — accessible uniquement depuis l'EC2 API"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL depuis API"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "vpp-sg-rds-${var.environment}" }
}

# =============================================================================
# IAM — profil instance EC2
# =============================================================================

resource "aws_iam_role" "ec2_api" {
  name = "vpp-ec2-api-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ec2_api_s3" {
  name = "vpp-ec2-s3-${var.environment}"
  role = aws_iam_role.ec2_api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]
        Resource = [
          aws_s3_bucket.logs.arn,
          "${aws_s3_bucket.logs.arn}/*",
          aws_s3_bucket.backups.arn,
          "${aws_s3_bucket.backups.arn}/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:*:secret:vpp-italia/${var.environment}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/vpp-italia/${var.environment}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ec2_api" {
  name = "vpp-ec2-api-${var.environment}"
  role = aws_iam_role.ec2_api.name
}

# =============================================================================
# EC2 — serveur API FastAPI (t3.medium)
# =============================================================================

resource "aws_key_pair" "deploy" {
  key_name   = "vpp-deploy-${var.environment}"
  public_key = var.ec2_public_key
}

resource "aws_instance" "api" {
  ami                    = var.ec2_ami_id
  instance_type          = var.ec2_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.api.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2_api.name
  key_name               = aws_key_pair.deploy.key_name

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 30
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/userdata.sh", {
    environment         = var.environment
    aws_region          = var.aws_region
    s3_logs_bucket_name = aws_s3_bucket.logs.bucket
    s3_backups_bucket   = aws_s3_bucket.backups.bucket
  })

  tags = { Name = "vpp-api-${var.environment}" }

  lifecycle {
    # Ne pas recréer l'instance si l'AMI change (déploiement via SSH)
    ignore_changes = [ami, user_data]
  }
}

# IP Elastic pour une adresse publique stable
resource "aws_eip" "api" {
  instance = aws_instance.api.id
  domain   = "vpc"
  tags     = { Name = "vpp-eip-api-${var.environment}" }
}

# =============================================================================
# RDS — PostgreSQL 15 / TimescaleDB (db.t3.micro)
# =============================================================================

resource "aws_db_subnet_group" "main" {
  name       = "vpp-italia-${var.environment}"
  subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_b.id]
  tags       = { Name = "vpp-db-subnet-${var.environment}" }
}

resource "aws_db_instance" "timescaledb" {
  identifier        = "vpp-italia-${var.environment}"
  engine            = "postgres"
  engine_version    = "15.5"
  instance_class    = var.db_instance_class
  allocated_storage = 20
  max_allocated_storage = 100
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = "vpp_italia"
  username = "vpp"
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  multi_az               = false
  publicly_accessible    = false
  deletion_protection    = var.environment == "production"
  skip_final_snapshot    = var.environment != "production"
  final_snapshot_identifier = var.environment == "production" ? "vpp-italia-final-snapshot" : null

  backup_retention_period = 7
  backup_window           = "02:00-03:00"
  maintenance_window      = "sun:03:00-sun:04:00"

  # Paramètres TimescaleDB — nécessite un parameter group personnalisé
  parameter_group_name = aws_db_parameter_group.timescaledb.name

  tags = { Name = "vpp-rds-${var.environment}" }
}

resource "aws_db_parameter_group" "timescaledb" {
  name   = "vpp-timescaledb-${var.environment}"
  family = "postgres15"

  parameter {
    name  = "shared_preload_libraries"
    value = "timescaledb"
  }

  parameter {
    name  = "max_connections"
    value = "200"
  }

  tags = { Name = "vpp-pg-timescaledb-${var.environment}" }
}

# =============================================================================
# S3 — logs applicatifs
# =============================================================================

resource "aws_s3_bucket" "logs" {
  bucket        = "vpp-italia-logs-${var.environment}-${var.aws_account_id}"
  force_destroy = var.environment != "production"

  tags = { Name = "vpp-s3-logs-${var.environment}" }
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Logs conservés 30 jours puis supprimés
resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "logs-expiry"
    status = "Enabled"
    filter { prefix = "" }
    expiration { days = 30 }
  }
}

# =============================================================================
# S3 — backups BDD
# =============================================================================

resource "aws_s3_bucket" "backups" {
  bucket        = "vpp-italia-backups-${var.environment}-${var.aws_account_id}"
  force_destroy = var.environment != "production"

  tags = { Name = "vpp-s3-backups-${var.environment}" }
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Backups → Glacier après 30 jours, supprimés après 90 jours
resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "backups-glacier"
    status = "Enabled"
    filter { prefix = "" }
    transition {
      days          = 30
      storage_class = "GLACIER"
    }
    expiration { days = 90 }
  }
}

# =============================================================================
# CloudWatch — logs API
# =============================================================================

resource "aws_cloudwatch_log_group" "api" {
  name              = "/vpp/api/${var.environment}"
  retention_in_days = 30
  tags              = { Name = "vpp-logs-api-${var.environment}" }
}

# =============================================================================
# Secrets Manager — credentials applicatifs
# =============================================================================

resource "aws_secretsmanager_secret" "db_url" {
  name                    = "vpp-italia/${var.environment}/database-url"
  recovery_window_in_days = var.environment == "production" ? 7 : 0
}

resource "aws_secretsmanager_secret" "jwt_secret" {
  name                    = "vpp-italia/${var.environment}/jwt-secret-key"
  recovery_window_in_days = var.environment == "production" ? 7 : 0
}

resource "aws_secretsmanager_secret" "gme_password" {
  name                    = "vpp-italia/${var.environment}/gme-api-password"
  recovery_window_in_days = var.environment == "production" ? 7 : 0
}

resource "aws_secretsmanager_secret" "terna_client_secret" {
  name                    = "vpp-italia/${var.environment}/terna-client-secret"
  recovery_window_in_days = var.environment == "production" ? 7 : 0
}
