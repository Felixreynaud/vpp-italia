#!/bin/bash
# Script d'initialisation EC2 — VPP Italia
# Exécuté au premier démarrage via EC2 user-data.
# Peut aussi être relancé manuellement pour réinitialiser un serveur.
set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================
APP_DIR="/opt/vpp-italia"
APP_USER="vpp"
SERVICE_NAME="vpp-api"
GITHUB_REPO="https://github.com/felixreynaud/vpp-italia.git"
LOG_FILE="/var/log/vpp-setup.log"

# Ces variables sont injectées par Terraform via templatefile()
AWS_REGION="${AWS_REGION:-eu-south-1}"
ENVIRONMENT="${ENVIRONMENT:-staging}"
S3_LOGS_BUCKET="${S3_LOGS_BUCKET:-}"
SSM_PREFIX="/vpp-italia/${ENVIRONMENT}"

log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG_FILE"
}

# =============================================================================
# 1 — Système
# =============================================================================
log "=== VPP Italia — setup (env: $ENVIRONMENT) ==="

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    git \
    curl \
    jq \
    awscli \
    libpq-dev \
    gcc \
    build-essential \
    nginx \
    postgresql-client-15

log "Packages installés"

# =============================================================================
# 2 — Utilisateur applicatif
# =============================================================================
if ! id "$APP_USER" &>/dev/null; then
    useradd -m -s /bin/bash -d "/home/$APP_USER" "$APP_USER"
    log "Utilisateur $APP_USER créé"
fi

# =============================================================================
# 3 — CloudWatch Agent
# =============================================================================
log "Installation CloudWatch Agent..."
if ! command -v /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl &>/dev/null; then
    wget -q \
        https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb \
        -O /tmp/cwagent.deb
    dpkg -i /tmp/cwagent.deb
    rm /tmp/cwagent.deb
fi

cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<CWJSON
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/vpp-api.log",
            "log_group_name": "/vpp/api/$ENVIRONMENT",
            "log_stream_name": "{instance_id}/api",
            "timestamp_format": "%Y-%m-%dT%H:%M:%S"
          },
          {
            "file_path": "/var/log/vpp-setup.log",
            "log_group_name": "/vpp/api/$ENVIRONMENT",
            "log_stream_name": "{instance_id}/setup"
          }
        ]
      }
    }
  },
  "metrics": {
    "metrics_collected": {
      "mem": { "measurement": ["mem_used_percent"] },
      "disk": {
        "measurement": ["used_percent"],
        "resources": ["/"]
      }
    }
  }
}
CWJSON

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config -m ec2 \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s || true

log "CloudWatch Agent configuré"

# =============================================================================
# 4 — Application VPP Italia
# =============================================================================
log "Clonage du dépôt..."
if [ -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git -C "$APP_DIR" fetch origin main
    sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard origin/main
else
    mkdir -p "$APP_DIR"
    chown "$APP_USER:$APP_USER" "$APP_DIR"
    sudo -u "$APP_USER" git clone "$GITHUB_REPO" "$APP_DIR"
fi

log "Création du virtualenv Python 3.11..."
sudo -u "$APP_USER" python3.11 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip -q
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

log "Dépendances installées"

# =============================================================================
# 5 — Variables d'environnement depuis AWS SSM Parameter Store
# =============================================================================
log "Récupération des paramètres SSM..."

fetch_ssm() {
    local param_name="$1"
    aws ssm get-parameter \
        --name "${SSM_PREFIX}/${param_name}" \
        --with-decryption \
        --region "$AWS_REGION" \
        --query "Parameter.Value" \
        --output text 2>/dev/null || echo ""
}

DATABASE_URL=$(fetch_ssm "database-url")
JWT_SECRET_KEY=$(fetch_ssm "jwt-secret-key")
GME_API_PASSWORD=$(fetch_ssm "gme-api-password")
TERNA_CLIENT_SECRET=$(fetch_ssm "terna-client-secret")
HUAWEI_CLIENT_SECRET=$(fetch_ssm "huawei-client-secret")

cat > "$APP_DIR/.env" <<ENV
# Généré automatiquement par setup_server.sh — $(date -u '+%Y-%m-%dT%H:%M:%SZ')
APP_ENV=${ENVIRONMENT}
API_HOST=0.0.0.0
API_PORT=8000
TIMEZONE=Europe/Rome
LOG_LEVEL=INFO

DATABASE_URL=${DATABASE_URL}
JWT_SECRET_KEY=${JWT_SECRET_KEY}

AWS_REGION=${AWS_REGION}
AWS_S3_BUCKET_LOGS=${S3_LOGS_BUCKET}

GME_ZONE=SUD
GME_API_PASSWORD=${GME_API_PASSWORD}

TERNA_CLIENT_SECRET=${TERNA_CLIENT_SECRET}

HUAWEI_DOMAIN=eu5.fusionsolar.huawei.com
HUAWEI_CLIENT_SECRET=${HUAWEI_CLIENT_SECRET}
HUAWEI_USE_SIMULATOR=false

DISPATCH_SOC_MIN=10
DISPATCH_SOC_MAX=90
DISPATCH_PRICE_THRESHOLD=0.5
ENV

chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"
log "Fichier .env créé"

# =============================================================================
# 6 — Migrations Alembic
# =============================================================================
if [ -f "$APP_DIR/alembic.ini" ]; then
    log "Application des migrations Alembic..."
    sudo -u "$APP_USER" \
        env $(cat "$APP_DIR/.env" | grep -v '^#' | xargs) \
        "$APP_DIR/.venv/bin/alembic" -c "$APP_DIR/alembic.ini" upgrade head || \
        log "WARN: migrations Alembic échouées (DB peut-être pas encore prête)"
fi

# =============================================================================
# 7 — Service systemd
# =============================================================================
log "Configuration du service systemd..."

cat > /etc/systemd/system/${SERVICE_NAME}.service <<SERVICE
[Unit]
Description=VPP Italia FastAPI
Documentation=https://github.com/felixreynaud/vpp-italia
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/uvicorn api.main:app \\
    --host 0.0.0.0 \\
    --port 8000 \\
    --workers 2 \\
    --log-level info \\
    --access-log
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=3
StandardOutput=append:/var/log/vpp-api.log
StandardError=append:/var/log/vpp-api.log

[Install]
WantedBy=multi-user.target
SERVICE

touch /var/log/vpp-api.log
chown "$APP_USER:$APP_USER" /var/log/vpp-api.log
chmod 644 /var/log/vpp-api.log

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# =============================================================================
# 8 — Script de déploiement (utilisé par GitHub Actions)
# =============================================================================
cat > /usr/local/bin/vpp-deploy <<'DEPLOY'
#!/bin/bash
set -euo pipefail
APP_DIR="/opt/vpp-italia"
APP_USER="vpp"
SERVICE="vpp-api"

echo "[deploy $(date -u '+%H:%M:%SZ')] Pull dernière version..."
sudo -u "$APP_USER" git -C "$APP_DIR" fetch origin main
sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard origin/main

echo "[deploy] Mise à jour des dépendances..."
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "[deploy] Migrations..."
sudo -u "$APP_USER" \
    env $(cat "$APP_DIR/.env" | grep -v '^#' | xargs) \
    "$APP_DIR/.venv/bin/alembic" -c "$APP_DIR/alembic.ini" upgrade head 2>/dev/null || true

echo "[deploy] Redémarrage du service..."
systemctl restart "$SERVICE"
sleep 3
systemctl status "$SERVICE" --no-pager --lines=5

echo "[deploy] ✓ Déploiement terminé."
DEPLOY
chmod +x /usr/local/bin/vpp-deploy

# =============================================================================
# 9 — Vérification finale
# =============================================================================
log "Attente démarrage API (15s)..."
sleep 15

if curl -sf http://localhost:8000/health > /dev/null; then
    log "✓ API VPP Italia opérationnelle"
else
    log "WARN: API non accessible sur /health — vérifier les logs : journalctl -u vpp-api"
fi

log "=== Setup terminé ==="
