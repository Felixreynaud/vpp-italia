# VPP Italia

Centrale virtuelle (Virtual Power Plant) pilotant un parc de 100+ batteries industrielles en Italie, avec participation aux marchés de l'énergie GME et Terna.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        VPP Italia                               │
│                                                                 │
│  ┌──────────┐    ┌────────────┐    ┌──────────────────────┐   │
│  │ Batteries│───▶│ Connectors │───▶│   Core Optimizer     │   │
│  │ 100+ BESS│    │Modbus/OCPP │    │   LP/MILP dispatch   │   │
│  └──────────┘    └────────────┘    └──────────┬───────────┘   │
│                                               │                 │
│  ┌──────────────┐    ┌──────────────────────────────────────┐  │
│  │  Marchés IT  │◀───│         API FastAPI                  │  │
│  │  GME / Terna │───▶│    /api/v1/batteries                 │  │
│  └──────────────┘    │    /api/v1/dispatch                  │  │
│                       │    /api/v1/markets                   │  │
│  ┌──────────────┐    └──────────────────────────────────────┘  │
│  │ TimescaleDB  │                      │                        │
│  │ (PostgreSQL) │◀─────────────────────┘                       │
│  └──────────────┘                                               │
│                                                                 │
│  ┌──────────────────────────────┐                              │
│  │  Monitoring: Grafana + PD    │                              │
│  └──────────────────────────────┘                              │
└─────────────────────────────────────────────────────────────────┘
```

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| Backend | Python 3.11, FastAPI |
| Base de données | PostgreSQL 15 + TimescaleDB 2.x |
| Message broker | Apache Kafka (Amazon MSK) |
| Protocoles batteries | Modbus TCP, OCPP 2.0.1, REST |
| Optimisation | PuLP / OR-Tools (LP/MILP) |
| Infrastructure | AWS eu-south-1 (Milan), Terraform |
| CI/CD | GitHub Actions |
| Monitoring | Grafana + Prometheus + PagerDuty |
| Conteneurs | Docker, ECS Fargate |

## Structure du projet

```
vpp-italia/
├── api/                    # Backend FastAPI
│   ├── main.py             # Point d'entrée, configuration app
│   ├── dependencies.py     # Injection de dépendances (DB, auth)
│   └── routes/             # Endpoints REST
│       ├── batteries.py    # CRUD + état temps réel batteries
│       ├── dispatch.py     # Plans de dispatch et commandes
│       └── markets.py      # Soumissions offres GME/Terna
├── core/                   # Moteur métier
│   ├── optimizer.py        # Optimiseur LP/MILP dispatch
│   ├── dispatch.py         # Exécution des plans de dispatch
│   └── scheduler.py        # Scheduler fenêtres de marché
├── connectors/             # Drivers protocoles
│   ├── modbus.py           # Client Modbus TCP
│   ├── ocpp.py             # Client OCPP 2.0.1
│   ├── gme.py              # Client API GME
│   └── terna.py            # Client API Terna
├── data/                   # Couche données
│   ├── models.py           # Modèles SQLAlchemy (ORM)
│   ├── schemas.py          # Schémas Pydantic (validation)
│   └── database.py         # Session DB, configuration
├── infra/
│   ├── terraform/          # Infrastructure AWS
│   └── .github/workflows/  # CI/CD GitHub Actions
├── monitoring/
│   ├── grafana/            # Dashboards JSON
│   └── alerts/             # Règles d'alerte Prometheus/PD
├── tests/                  # Suite de tests
│   ├── unit/               # Tests unitaires
│   └── integration/        # Tests d'intégration
└── docs/                   # Documentation
    ├── architecture.md     # Design décisions
    └── api.md              # Documentation API
```

## Prérequis

- Python 3.11+
- PostgreSQL 15 avec extension TimescaleDB
- Docker & Docker Compose (développement local)
- Accès AWS (pour le déploiement)
- Credentials GME et Terna (sandbox pour le développement)

## Démarrage rapide

```bash
# Cloner le dépôt
git clone https://github.com/felixreynaud/vpp-italia.git
cd vpp-italia

# Configurer l'environnement
cp .env.example .env
# Éditer .env avec vos credentials

# Démarrer les services locaux (DB, Kafka)
docker compose up -d

# Installer les dépendances Python
pip install -r requirements.txt

# Appliquer les migrations
alembic upgrade head

# Lancer l'API en développement
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

L'API est disponible sur `http://localhost:8000` — docs interactives sur `/docs`.

## Développement

```bash
# Linter et formateur
ruff check .
ruff format .
mypy .

# Tests
pytest tests/ -v --cov=. --cov-report=html

# Tests d'intégration (nécessite DB locale)
pytest tests/integration/ -v -m integration
```

## Conventions

- Branches : `feature/`, `fix/`, `chore/` + description courte
- Commits : conventionnel (`feat:`, `fix:`, `chore:`, `docs:`)
- PR obligatoire sur `main` avec review
- Coverage minimum : 80%

Voir `CLAUDE.md` pour le guide complet d'architecture et les conventions de code.

## Marchés électriques italiens

La VPP participe à :
- **MGP** (Mercato del Giorno Prima) — marché spot J-1
- **MSD** (Mercato Servizi Dispacciamento) — services système, principale source de revenus
- **MB** (Mercato di Bilanciamento) — équilibrage temps réel

Toutes les heures sont en **Europe/Rome** (CET/CEST). L'unité temporelle de base est le **quart d'heure (QH)**.

## Licence

Propriétaire — tous droits réservés.
