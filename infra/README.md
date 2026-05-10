# Infrastructure VPP Italia

Ce dossier contient toute la configuration d'infrastructure pour le déploiement
de la VPP Italia en production sur AWS eu-south-1 (Milan).

---

## Architecture réseau

```
Internet
    │
    │  :8000 (API)   :22 (SSH admin uniquement)
    ▼
┌──────────────────────────────────────────────────────────────┐
│  VPC vpp-italia  —  10.0.0.0/16   (eu-south-1)              │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Sous-réseau PUBLIC  —  10.0.1.0/24  (AZ: a)       │    │
│  │                                                      │    │
│  │   ┌─────────────────────────────────────┐           │    │
│  │   │  EC2 t3.medium  (API FastAPI)        │           │    │
│  │   │  IP Elastic fixe                     │           │    │
│  │   │  SG: port 8000 ouvert               │           │    │
│  │   │       port 22 → admin_cidr seulement│           │    │
│  │   └──────────────────┬──────────────────┘           │    │
│  └─────────────────────-│────────────────────────────-─┘    │
│                          │ PostgreSQL :5432                   │
│                          │ (SG RDS: source = SG API seulement)│
│  ┌───────────────────────▼──────────────────────────────┐    │
│  │  Sous-réseau PRIVÉ A  —  10.0.10.0/24  (AZ: a)      │    │
│  │  Sous-réseau PRIVÉ B  —  10.0.11.0/24  (AZ: b)      │    │
│  │                                                       │    │
│  │   ┌──────────────────────────────────────────────┐   │    │
│  │   │  RDS db.t3.micro  (PostgreSQL 15 + TimescaleDB)│  │    │
│  │   │  Aucun accès Internet direct                 │   │    │
│  │   └──────────────────────────────────────────────┘   │    │
│  └───────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘

     ┌──────────────────────┐  ┌──────────────────────────┐
     │  S3 logs (chiffré)   │  │  S3 backups (chiffré)    │
     │  logs applicatifs    │  │  dumps PostgreSQL         │
     │  Lifecycle : 30j     │  │  Glacier 30j → supprimé 90j│
     └──────────────────────┘  └──────────────────────────┘

     ┌──────────────────┐
     │  Secrets Manager │  ← DATABASE_URL, JWT_SECRET, GME_PASSWORD, TERNA_SECRET
     └──────────────────┘
```

---

## Structure des fichiers Terraform

```
infra/terraform/
├── main.tf                  # Ressources AWS (VPC, EC2, RDS, S3, IAM...)
├── variables.tf             # Déclaration de toutes les variables
├── outputs.tf               # Valeurs exportées après apply (IP, endpoints...)
├── userdata.sh              # Script de bootstrap de l'instance EC2
└── terraform.tfvars.example # Exemple de configuration (copier → terraform.tfvars)
```

---

## Prérequis avant de commencer

1. **AWS CLI** configuré avec les droits suffisants :
   ```bash
   aws configure
   # ou via variables d'environnement :
   export AWS_ACCESS_KEY_ID=...
   export AWS_SECRET_ACCESS_KEY=...
   export AWS_DEFAULT_REGION=eu-south-1
   ```

2. **Terraform >= 1.7** installé :
   ```bash
   terraform -version
   ```

3. **Clé SSH** générée pour l'accès EC2 :
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/vpp-deploy -C "vpp-italia-deploy"
   # La clé publique (~/.ssh/vpp-deploy.pub) est à mettre dans terraform.tfvars
   ```

4. **Connaître son ID de compte AWS** :
   ```bash
   aws sts get-caller-identity --query Account --output text
   ```

---

## Déploiement Terraform — commandes exactes

### Étape 1 — Configurer les variables

```bash
cd infra/terraform/

# Copier l'exemple et compléter les valeurs
cp terraform.tfvars.example terraform.tfvars
nano terraform.tfvars   # Renseigner : aws_account_id, db_password, ec2_public_key, admin_cidr
```

**Variables obligatoires à renseigner dans `terraform.tfvars` :**

| Variable | Description | Exemple |
|----------|-------------|---------|
| `aws_account_id` | ID du compte AWS | `"123456789012"` |
| `db_password` | Mot de passe PostgreSQL (≥ 16 chars) | `"MonMotDePasse2024!"` |
| `ec2_public_key` | Contenu de `~/.ssh/vpp-deploy.pub` | `"ssh-ed25519 AAAA..."` |
| `admin_cidr` | Votre IP pour SSH (`/32`) | `"203.0.113.5/32"` |

Récupérer votre IP actuelle :
```bash
curl -s ifconfig.me
# → ajouter /32 : ex. "203.0.113.5/32"
```

### Étape 2 — Initialiser Terraform

```bash
terraform init
```

### Étape 3 — Vérifier le plan

```bash
terraform plan -var-file=terraform.tfvars
```

Vérifier attentivement les ressources qui vont être créées. Aucune modification
n'est effectuée à cette étape.

### Étape 4 — Appliquer l'infrastructure

```bash
terraform apply -var-file=terraform.tfvars
# Taper 'yes' pour confirmer
```

La création complète prend environ **8-12 minutes** (RDS est le plus long).

### Étape 5 — Récupérer les outputs

```bash
terraform output
```

Exemple de sortie :
```
ec2_public_ip          = "15.161.42.87"
ec2_public_dns         = "ec2-15-161-42-87.eu-south-1.compute.amazonaws.com"
api_ssh_command        = "ssh -i ~/.ssh/vpp-deploy ubuntu@15.161.42.87"
api_url                = "http://15.161.42.87:8000"
rds_endpoint           = "vpp-italia-staging.xxxx.eu-south-1.rds.amazonaws.com:5432"
s3_logs_bucket_name    = "vpp-italia-logs-staging-123456789012"
s3_backups_bucket_name = "vpp-italia-backups-staging-123456789012"
```

### Étape 6 — Renseigner les secrets dans AWS Secrets Manager

```bash
# DATABASE_URL (utiliser l'endpoint RDS obtenu en step 5)
aws secretsmanager put-secret-value \
  --secret-id "vpp-italia/staging/database-url" \
  --secret-string "postgresql+asyncpg://vpp:MonMotDePasse2024!@vpp-italia-staging.xxxx.eu-south-1.rds.amazonaws.com:5432/vpp_italia" \
  --region eu-south-1

# JWT secret key
aws secretsmanager put-secret-value \
  --secret-id "vpp-italia/staging/jwt-secret-key" \
  --secret-string "$(openssl rand -hex 64)" \
  --region eu-south-1

# Credentials GME
aws secretsmanager put-secret-value \
  --secret-id "vpp-italia/staging/gme-api-password" \
  --secret-string "votre-mot-de-passe-gme" \
  --region eu-south-1

# Credentials Terna
aws secretsmanager put-secret-value \
  --secret-id "vpp-italia/staging/terna-client-secret" \
  --secret-string "votre-client-secret-terna" \
  --region eu-south-1
```

### Étape 7 — Vérifier l'API

```bash
# Attendre ~2 minutes que le service démarre
curl http://<api_public_ip>:8000/health
# → {"status": "ok", "version": "0.1.0"}
```

### Détruire l'infrastructure (dev/staging uniquement)

```bash
terraform destroy -var-file=terraform.tfvars
# ATTENTION : supprime toutes les ressources, irréversible en production
```

---

## GitHub Actions — configuration des secrets

Les workflows CI/CD nécessitent des secrets configurés dans GitHub.

**Navigation :** `Settings → Secrets and variables → Actions → New repository secret`

### Secrets requis

| Nom du secret | Description | Comment l'obtenir |
|---------------|-------------|-------------------|
| `AWS_ACCESS_KEY_ID` | Clé d'accès AWS | Console IAM → Utilisateurs → Clés d'accès |
| `AWS_SECRET_ACCESS_KEY` | Secret de la clé AWS | Lors de la création de la clé IAM |
| `EC2_HOST` | IP publique de l'EC2 | `terraform output api_public_ip` |
| `EC2_SSH_PRIVATE_KEY` | Clé SSH privée (contenu complet) | `cat ~/.ssh/vpp-deploy` |

### Créer un utilisateur IAM dédié pour GitHub Actions

Il est fortement recommandé de créer un utilisateur IAM avec des droits **minimaux** :

```bash
# Créer l'utilisateur
aws iam create-user --user-name vpp-github-actions

# Attacher une policy personnalisée (droits minimaux pour EC2 SSH + CloudWatch)
aws iam put-user-policy \
  --user-name vpp-github-actions \
  --policy-name vpp-deploy-policy \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": [
          "ec2:DescribeInstances",
          "cloudwatch:GetMetricData",
          "logs:DescribeLogGroups"
        ],
        "Resource": "*"
      }
    ]
  }'

# Créer les clés d'accès
aws iam create-access-key --user-name vpp-github-actions
```

> Note : le déploiement se fait via SSH directement sur l'EC2, donc les
> droits AWS de l'utilisateur GitHub Actions sont limités aux vérifications
> post-déploiement.

### Environnement de protection GitHub

Pour la branche `main`, activer la protection dans :
`Settings → Environments → production → Required reviewers`

Cela ajoutera une approbation manuelle avant chaque déploiement en production.

---

## Workflows CI/CD

### `test.yml` — Déclenché sur chaque Pull Request

```
PR ouverte
    │
    ├── lint (Ruff + mypy)
    │
    └── unit-tests (SQLite in-memory)
            │
            └── integration-tests (TimescaleDB via service Docker)
```

### `deploy.yml` — Déclenché sur chaque push sur `main`

```
Push sur main
    │
    ├── pre-deploy-tests (tests unitaires)
    │
    └── deploy (approbation manuelle requise en production)
            │
            ├── SSH → vpp-deploy (git pull + pip install + alembic + restart)
            │
            └── smoke-test (GET /health → 200)
```

---

## Monitoring Grafana

Les fichiers de configuration Grafana se trouvent dans `monitoring/grafana/`.

### Importer la datasource

```bash
# Depuis le serveur Grafana, remplacer les variables d'environnement
export RDS_ENDPOINT=$(terraform output -raw rds_endpoint)
export DB_PASSWORD="votre-mot-de-passe-rds"

envsubst < ../monitoring/grafana/datasource.json | \
  curl -s -X POST http://admin:admin@localhost:3000/api/datasources \
  -H "Content-Type: application/json" -d @-
```

### Importer les dashboards

```bash
# Dashboard flotte batteries
curl -s -X POST http://admin:admin@localhost:3000/api/dashboards/import \
  -H "Content-Type: application/json" \
  -d "{\"dashboard\": $(cat ../monitoring/grafana/dashboards/dashboard_batteries.json), \"overwrite\": true}"
```

### Importer les alertes

```bash
curl -s -X POST http://admin:admin@localhost:3000/api/ruler/grafana/api/v1/rules/VPP%20Italia \
  -H "Content-Type: application/json" \
  -d @../monitoring/grafana/alerts.json
```

> Pour Grafana Cloud, remplacer `http://admin:admin@localhost:3000` par
> l'URL de votre instance avec un API key Bearer.

---

## Coût estimé (eu-south-1)

| Ressource | Spec | Coût mensuel estimé |
|-----------|------|---------------------|
| EC2 t3.medium | 2 vCPU, 4 GB RAM | ~35 USD |
| RDS db.t3.micro | 1 vCPU, 1 GB RAM, 20 GB gp3 | ~25 USD |
| S3 logs | Logs applicatifs 30 jours | ~0.5 USD |
| S3 backups | Backups BDD (Glacier) | ~0.5 USD |
| IP Elastic | 1 adresse | ~4 USD |
| CloudWatch Logs | ~5 GB/mois | ~3 USD |
| **Total estimé** | | **~68 USD/mois** |

> Les prix AWS eu-south-1 peuvent varier. Vérifier le [calculateur AWS](https://calculator.aws/).
