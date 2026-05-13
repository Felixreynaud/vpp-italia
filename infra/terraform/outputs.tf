# =============================================================================
# Outputs Terraform — valeurs utiles après apply
# =============================================================================

output "api_public_ip" {
  description = "IP publique Elastic de l'instance EC2 API (stable, ne change pas au redémarrage)"
  value       = aws_eip.api.public_ip
}

output "ec2_public_ip" {
  description = "Alias — IP publique Elastic EC2 (identique à api_public_ip)"
  value       = aws_eip.api.public_ip
}

output "ec2_public_dns" {
  description = "DNS public de l'instance EC2 (peut changer au redémarrage — préférer l'EIP)"
  value       = aws_instance.api.public_dns
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

output "s3_logs_bucket_name" {
  description = "Nom du bucket S3 pour les logs applicatifs"
  value       = aws_s3_bucket.logs.bucket
}

output "s3_backups_bucket_name" {
  description = "Nom du bucket S3 pour les backups BDD"
  value       = aws_s3_bucket.backups.bucket
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
  value       = data.aws_vpc.main.id
}

output "cloudwatch_log_group" {
  description = "Groupe de logs CloudWatch pour l'API"
  value       = aws_cloudwatch_log_group.api.name
}

# -----------------------------------------------------------------------------
# Cloudflare Tunnel — URL publique HTTPS (générée à chaque boot par cloudflared)
# -----------------------------------------------------------------------------

output "cloudflare_tunnel_url_command" {
  description = "Commande à lancer pour récupérer l'URL publique HTTPS du tunnel Cloudflare (servie via Nginx -> frontend + /api/ + /grafana/)."
  value       = "aws ssm get-parameter --name /vpp-italia/${var.environment}/cloudflare-tunnel-url --region ${var.aws_region} --query Parameter.Value --output text"
}

output "post_deploy_urls_hint" {
  description = "Récap : récupérer l'URL Cloudflare puis ouvrir frontend / API / Grafana"
  value = join("\n", [
    "1) URL publique HTTPS (à attendre ~3-5 min après apply, le temps que userdata finisse) :",
    "     aws ssm get-parameter --name /vpp-italia/${var.environment}/cloudflare-tunnel-url --region ${var.aws_region} --query Parameter.Value --output text",
    "2) Une fois récupérée (ex: https://xyz-abc.trycloudflare.com), ouvrir dans le navigateur :",
    "     <URL>/            -> Frontend React",
    "     <URL>/api/v1/...  -> API FastAPI",
    "     <URL>/grafana/    -> Grafana (login admin/admin au premier accès)",
  ])
}
