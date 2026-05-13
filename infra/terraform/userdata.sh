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
    postgresql-client \
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
# Variables d'environnement depuis SSM Parameter Store
# Les paramètres sont provisionnés par le workflow Terraform après l'apply.
# Si absents au premier boot, l'API démarre sans DB (mode dégradé).
# -----------------------------------------------------------------------------
log "Récupération des paramètres SSM..."
SSM_PREFIX="/vpp-italia/$ENVIRONMENT"

DB_URL=$(aws ssm get-parameter \
    --name "$SSM_PREFIX/database-url" \
    --with-decryption \
    --region "$AWS_REGION" \
    --query Parameter.Value --output text 2>/dev/null || echo "")

JWT_SECRET=$(aws ssm get-parameter \
    --name "$SSM_PREFIX/jwt-secret-key" \
    --with-decryption \
    --region "$AWS_REGION" \
    --query Parameter.Value --output text 2>/dev/null || echo "changeme-set-in-ssm")

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

# =============================================================================
# Node.js 20 (pour builder le frontend React)
# =============================================================================
log "Installation Node.js 20..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1
apt-get install -y -qq nodejs

# =============================================================================
# Build frontend React (Vite)
# =============================================================================
log "Build du frontend React..."
if [ -d "$APP_DIR/frontend" ]; then
    cd "$APP_DIR/frontend"
    # baseURL vide => le client API fait des appels relatifs interceptés par Nginx
    sudo -u "$APP_USER" tee .env.production >/dev/null <<'ENVPROD'
VITE_API_URL=
VITE_MOCK_DATA=false
ENVPROD
    sudo -u "$APP_USER" npm install --silent --no-audit --no-fund
    sudo -u "$APP_USER" npm run build
    log "Frontend buildé dans $APP_DIR/frontend/dist"
else
    log "ATTENTION : dossier $APP_DIR/frontend introuvable, build frontend ignoré"
fi

# =============================================================================
# Prometheus (binaire officiel)
# =============================================================================
log "Installation Prometheus..."
PROM_VERSION="2.51.0"
useradd --no-create-home --shell /usr/sbin/nologin prometheus || true
mkdir -p /etc/prometheus /var/lib/prometheus
cd /tmp
wget -q "https://github.com/prometheus/prometheus/releases/download/v$PROM_VERSION/prometheus-$PROM_VERSION.linux-amd64.tar.gz"
tar xzf "prometheus-$PROM_VERSION.linux-amd64.tar.gz"
install -m 755 "prometheus-$PROM_VERSION.linux-amd64/prometheus" /usr/local/bin/prometheus
install -m 755 "prometheus-$PROM_VERSION.linux-amd64/promtool" /usr/local/bin/promtool
rm -rf "prometheus-$PROM_VERSION.linux-amd64" "prometheus-$PROM_VERSION.linux-amd64.tar.gz"

# Config Prometheus — scrape de l'API FastAPI locale
cat > /etc/prometheus/prometheus.yml <<'PROM'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "vpp-api"
    static_configs:
      - targets: ["127.0.0.1:8000"]
    metrics_path: /metrics
PROM
chown -R prometheus:prometheus /etc/prometheus /var/lib/prometheus

cat > /etc/systemd/system/prometheus.service <<'SVC'
[Unit]
Description=Prometheus
After=network.target

[Service]
User=prometheus
Group=prometheus
Type=simple
ExecStart=/usr/local/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus --web.listen-address=127.0.0.1:9090
Restart=on-failure

[Install]
WantedBy=multi-user.target
SVC
systemctl daemon-reload
systemctl enable prometheus
systemctl start prometheus

# =============================================================================
# Grafana (paquet officiel APT)
# =============================================================================
log "Installation Grafana..."
mkdir -p /etc/apt/keyrings
curl -fsSL https://packages.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://packages.grafana.com/oss/deb stable main" > /etc/apt/sources.list.d/grafana.list
apt-get update -qq
apt-get install -y -qq grafana

# Configurer Grafana pour être servi sous /grafana/ derrière Nginx
sed -i 's|^;\?serve_from_sub_path *=.*|serve_from_sub_path = true|' /etc/grafana/grafana.ini
sed -i 's|^;\?root_url *=.*|root_url = %(protocol)s://%(domain)s/grafana/|' /etc/grafana/grafana.ini
sed -i 's|^;\?http_addr *=.*|http_addr = 127.0.0.1|' /etc/grafana/grafana.ini

# Provisionnage : copier les dashboards depuis le repo
mkdir -p /etc/grafana/provisioning/dashboards /etc/grafana/provisioning/datasources
if [ -d "$APP_DIR/monitoring/grafana/provisioning/dashboards" ]; then
    cp -r "$APP_DIR/monitoring/grafana/provisioning/dashboards/"* /etc/grafana/provisioning/dashboards/ || true
fi

# Generer les datasources avec les vraies URLs (RDS + Prometheus local)
RDS_HOSTPORT=$(echo "$DB_URL" | sed -n 's|.*@\([^/]*\)/.*|\1|p' || echo "")
RDS_PASSWORD=$(echo "$DB_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p' || echo "")

cat > /etc/grafana/provisioning/datasources/datasources.yml <<DS
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    uid: vpp-prometheus
    url: http://127.0.0.1:9090
    access: proxy
    isDefault: true
    editable: false

  - name: TimescaleDB
    type: postgres
    uid: vpp-timescaledb
    url: $RDS_HOSTPORT
    user: vpp
    database: vpp_italia
    secureJsonData:
      password: $RDS_PASSWORD
    jsonData:
      sslmode: require
      maxOpenConns: 5
      maxIdleConns: 2
      connMaxLifetime: 14400
      postgresVersion: 1500
      timescaledb: true
    isDefault: false
    editable: false
DS

chown -R grafana:grafana /etc/grafana
systemctl enable grafana-server
systemctl start grafana-server

# =============================================================================
# Nginx — reverse proxy (frontend / + API /api/ + Grafana /grafana/)
# =============================================================================
log "Configuration Nginx..."
cat > /etc/nginx/sites-available/vpp-italia <<'NGINX'
server {
    listen 80 default_server;
    server_name _;

    # ---- Frontend React (fichiers statiques) ----
    root /opt/vpp-italia/frontend/dist;
    index index.html;
    location / {
        try_files $uri $uri/ /index.html;
    }

    # ---- API FastAPI ----
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location = /health {
        proxy_pass http://127.0.0.1:8000/health;
    }

    # ---- Grafana ----
    # Pas de slash final sur proxy_pass : on transmet l'URL complète /grafana/...
    # à Grafana (qui a serve_from_sub_path=true). Sinon Nginx strippe /grafana/
    # et Grafana renvoie une redirection vers /grafana/, créant une boucle.
    location /grafana/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /grafana/api/live/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
NGINX
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/vpp-italia /etc/nginx/sites-enabled/vpp-italia
nginx -t
systemctl enable nginx
systemctl restart nginx

# =============================================================================
# Cloudflare Tunnel — quick tunnel (URL https://*.trycloudflare.com auto)
# =============================================================================
log "Installation Cloudflare Tunnel (cloudflared)..."
cd /tmp
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
dpkg -i cloudflared-linux-amd64.deb
rm cloudflared-linux-amd64.deb

cat > /etc/systemd/system/cloudflared.service <<'CF'
[Unit]
Description=Cloudflare Tunnel (quick tunnel vers Nginx local)
After=nginx.service
Wants=nginx.service

[Service]
Type=simple
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --url http://localhost:80
Restart=on-failure
RestartSec=10
StandardOutput=append:/var/log/cloudflared.log
StandardError=append:/var/log/cloudflared.log

[Install]
WantedBy=multi-user.target
CF
touch /var/log/cloudflared.log
systemctl daemon-reload
systemctl enable cloudflared
systemctl start cloudflared

# Attendre que le tunnel publie son URL, puis la stocker dans SSM
log "Attente de l'URL du tunnel Cloudflare..."
TUNNEL_URL=""
for i in $(seq 1 30); do
    sleep 2
    TUNNEL_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /var/log/cloudflared.log | head -1 || echo "")
    if [ -n "$TUNNEL_URL" ]; then
        break
    fi
done

if [ -n "$TUNNEL_URL" ]; then
    log "Tunnel Cloudflare actif : $TUNNEL_URL"

    # Met à jour le root_url de Grafana avec l'URL publique pour que ses
    # redirections (ex: /grafana/ -> /grafana/login) pointent vers le bon
    # hostname externe au lieu de localhost.
    sed -i "s|^root_url *=.*|root_url = $TUNNEL_URL/grafana/|" /etc/grafana/grafana.ini
    systemctl restart grafana-server || log "Echec redémarrage Grafana"

    # AWS CLI v1 interprète par défaut les valeurs commençant par http(s):// comme
    # des URIs à fetcher (cli_follow_urlparam). On désactive ce comportement pour
    # pouvoir stocker une URL telle quelle dans SSM.
    aws configure set cli_follow_urlparam false
    aws ssm put-parameter \
        --name "$SSM_PREFIX/cloudflare-tunnel-url" \
        --value "$TUNNEL_URL" \
        --type String --overwrite \
        --region "$AWS_REGION" 2>&1 | tee -a /var/log/vpp-userdata.log || log "Echec stockage SSM (vérifier IAM ssm:PutParameter)"
else
    log "ATTENTION : URL du tunnel non détectée après 60s. Voir /var/log/cloudflared.log"
fi

log "=== Provisionnage terminé ==="
