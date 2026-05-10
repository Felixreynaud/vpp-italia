# =============================================================================
# Outputs Terraform — valeurs utiles après apply
# =============================================================================

output "api_public_ip" {
  description = "IP publique Elastic de l'instance EC2 API (stable, ne change pas au redémarrage)"
  value       = aws_eip.api.public_ip
}

output "api_ssh_command" {
  description = "Commande SSH pour se connecter à l'instance API"
  value       = "ssh -i ~/.ssh/vpp-deploy ubuntu@${aws_eip.api.public_ip}"
}

output "api_url" {
  description = "URL de l'API FastAPI"
  value       = "http://${aws_eip.api.public_ip}:8000"
}

output "rds_endpoint" {
  description = "Endpoint RDS PostgreSQL (accessible uniquement depuis le VPC)"
  value       = aws_db_instance.timescaledb.endpoint
}

output "rds_port" {
  description = "Port RDS PostgreSQL"
  value       = aws_db_instance.timescaledb.port
}

output "database_url_template" {
  description = "Template DATABASE_URL à renseigner dans Secrets Manager (remplacer <PASSWORD>)"
  value       = "postgresql+asyncpg://vpp:<PASSWORD>@${aws_db_instance.timescaledb.endpoint}/vpp_italia"
  sensitive   = false
}

output "s3_bucket_name" {
  description = "Nom du bucket S3 pour les logs et backups"
  value       = aws_s3_bucket.vpp.bucket
}

output "secrets_manager_arns" {
  description = "ARNs des secrets à renseigner dans Secrets Manager"
  value = {
    database_url         = aws_secretsmanager_secret.db_url.arn
    jwt_secret           = aws_secretsmanager_secret.jwt_secret.arn
    gme_password         = aws_secretsmanager_secret.gme_password.arn
    terna_client_secret  = aws_secretsmanager_secret.terna_client_secret.arn
  }
}

output "vpc_id" {
  description = "ID du VPC VPP Italia"
  value       = aws_vpc.main.id
}

output "cloudwatch_log_group" {
  description = "Groupe de logs CloudWatch pour l'API"
  value       = aws_cloudwatch_log_group.api.name
}
