#!/bin/bash
# Script de démarrage EC2 — provisionning initial de l'instance API VPP Italia
# Exécuté une seule fois au premier démarrage de l'instance.
set -euo pipefail

ENVIRONMENT="${environment}"
AWS_REGION="${aws_region}"
S3_LOGS_BUCKET="${s3_logs_bucket_name}"
S3_BACKUPS_BUCKET="${s3_backups_bucket}"
APP_DIR="/opt/vpp-italia"
APP_USER="vpp"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a /var/log/vpp-userdata.log; }

log "=== VPP Italia — provisionnage EC2 (env: $ENVIRONMENT) ==="

# -----------------------------------------------------------------------------
# Système
# -----------------------------------------------------------------------------
log "Mise à jour système..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
    python3.11 python3.11-venv python3.11-dev \
    python3-pip \
    git \
    curl \
    jq \
    awscli \
    nginx \
    postgresql-client-15 \
    libpq-dev \
    gcc \
    build-essential

# -----------------------------------------------------------------------------
# Utilisateur applicatif
# -----------------------------------------------------------------------------
log "Création de l'utilisateur $APP_USER..."
useradd -m -s /bin/bash "$APP_USER" || true
mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"

# -----------------------------------------------------------------------------
# CloudWatch Agent
# -----------------------------------------------------------------------------
log "Installation CloudWatch Agent..."
wget -q https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb
dpkg -i amazon-cloudwatch-agent.deb || true
rm amazon-cloudwatch-agent.deb

cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<EOF
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/vpp-api.log",
            "log_group_name": "/vpp/api/$ENVIRONMENT",
            "log_stream_name": "{instance_id}",
            "timestamp_format": "%Y-%m-%dT%H:%M:%S"
          }
        ]
      }
    }
  }
}
EOF
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config -m ec2 \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s

# -----------------------------------------------------------------------------
# Application VPP Italia
# -----------------------------------------------------------------------------
log "Clonage du dépôt..."
sudo -u "$APP_USER" git clone https://github.com/felixreynaud/vpp-italia.git "$APP_DIR" || true

log "Création du virtualenv Python..."
sudo -u "$APP_USER" python3.11 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip -q
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# -----------------------------------------------------------------------------
# Variables d'environnement depuis Secrets Manager
# -----------------------------------------------------------------------------
log "Récupération des secrets AWS Secrets Manager..."
DB_URL=$(aws secretsmanager get-secret-value \
    --secret-id "vpp-italia/$ENVIRONMENT/database-url" \
    --region "$AWS_REGION" \
    --query SecretString --output text 2>/dev/null || echo "")

JWT_SECRET=$(aws secretsmanager get-secret-value \
    --secret-id "vpp-italia/$ENVIRONMENT/jwt-secret-key" \
    --region "$AWS_REGION" \
    --query SecretString --output text 2>/dev/null || echo "changeme-set-in-secrets-manager")

cat > "$APP_DIR/.env" <<ENV
APP_ENV=$ENVIRONMENT
DATABASE_URL=$DB_URL
JWT_SECRET_KEY=$JWT_SECRET
AWS_REGION=$AWS_REGION
AWS_S3_BUCKET_LOGS=$S3_LOGS_BUCKET
AWS_S3_BUCKET_BACKUPS=$S3_BACKUPS_BUCKET
TIMEZONE=Europe/Rome
LOG_LEVEL=INFO
ENV
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

# -----------------------------------------------------------------------------
# Service systemd
# -----------------------------------------------------------------------------
log "Configuration du service systemd vpp-api..."
cat > /etc/systemd/system/vpp-api.service <<SERVICE
[Unit]
Description=VPP Italia FastAPI
After=network.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/vpp-api.log
StandardError=append:/var/log/vpp-api.log

[Install]
WantedBy=multi-user.target
SERVICE

touch /var/log/vpp-api.log
chown "$APP_USER:$APP_USER" /var/log/vpp-api.log

systemctl daemon-reload
systemctl enable vpp-api
systemctl start vpp-api

# -----------------------------------------------------------------------------
# Script de déploiement (utilisé par GitHub Actions)
# -----------------------------------------------------------------------------
cat > /usr/local/bin/vpp-deploy <<'DEPLOY'
#!/bin/bash
set -euo pipefail
APP_DIR="/opt/vpp-italia"
APP_USER="vpp"

echo "[deploy] Pull latest code..."
sudo -u "$APP_USER" git -C "$APP_DIR" fetch origin main
sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard origin/main

echo "[deploy] Update dependencies..."
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "[deploy] Run migrations..."
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/alembic" -c "$APP_DIR/alembic.ini" upgrade head || true

echo "[deploy] Restart service..."
systemctl restart vpp-api
systemctl status vpp-api --no-pager

echo "[deploy] Done."
DEPLOY
chmod +x /usr/local/bin/vpp-deploy

log "=== Provisionnage terminé ==="
