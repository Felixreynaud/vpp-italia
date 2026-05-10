# VPP Italia — Guide pour Claude

## Contexte métier

Centrale virtuelle (VPP) pilotant un parc de 100+ batteries industrielles en Italie pour participer aux marchés de l'énergie opérés par :

- **GME** (Gestore dei Mercati Energetici) — marché spot électricité (MGP, MI, MSD)
- **Terna** — gestionnaire du réseau de transport, marchés de services système (MSD, MB)
- **MSD** (Mercato per il Servizio di Dispacciamento) — marché des services d'équilibrage, principal source de revenus

Les batteries participent en tant qu'**Unità di Produzione/Consumo Abilitata (UPCA)** ou via un **Aggregatore** agréé par Terna (décret TIDE).

## Architecture globale

```
[Batteries physiques] ←→ [Connectors: Modbus/OCPP/REST]
                                    ↓
                         [Core: Optimiseur dispatch]
                                    ↓
                    [API FastAPI] ←→ [TimescaleDB]
                         ↓                ↓
              [Marchés GME/Terna]   [Monitoring Grafana]
```

### Flux de données

1. Les connecteurs interrogent les batteries toutes les **10 secondes** (SoC, puissance, température, alertes)
2. L'optimiseur calcule le plan de dispatch toutes les **15 minutes** (horizon 24h glissant)
3. Les offres sont soumises à GME/Terna selon les fenêtres de marché :
   - MGP : J-1 avant 12h00
   - MI1-MI7 : fenêtres intra-day
   - MSD : sessions à J-1 et en temps réel
4. L'API expose les états et commandes en REST pour le front-end et les intégrations externes

## Structure des dossiers

```
/api          → Backend FastAPI : endpoints REST, authentification, WebSocket temps réel
/core         → Moteur d'optimisation LP/MILP, algorithmes de dispatch, scheduler
/connectors   → Drivers protocoles : Modbus TCP, OCPP 2.0.1, REST clients GME/Terna
/data         → Modèles SQLAlchemy, schémas Pydantic, migrations Alembic
/infra        → Terraform AWS (eu-south-1), GitHub Actions CI/CD
/monitoring   → Dashboards Grafana, règles Prometheus, alertes PagerDuty
/tests        → Tests unitaires (pytest), tests d'intégration, fixtures
/docs         → Documentation architecture, API OpenAPI, runbooks opérationnels
```

## Conventions de code

### Python
- **Version** : Python 3.11 (strict)
- **Formatter** : Ruff (`ruff format`) — line length 100
- **Linter** : Ruff (`ruff check`) + mypy strict
- **Type hints** : obligatoires sur toutes les fonctions publiques
- **Docstrings** : style Google, en anglais, uniquement sur les classes et fonctions publiques complexes
- **Tests** : pytest, coverage > 80%, fixtures dans `tests/conftest.py`

### Nommage
- Variables/fonctions : `snake_case`
- Classes : `PascalCase`
- Constantes : `UPPER_SNAKE_CASE`
- Fichiers : `snake_case.py`
- Tables DB : `snake_case` pluriel (ex: `battery_readings`)

### Git
- Branches : `feature/`, `fix/`, `chore/` + description courte
- Commits : conventionnel (`feat:`, `fix:`, `chore:`, `docs:`)
- Pas de commit direct sur `main` — PR obligatoire avec review

### API REST
- Versioning : `/api/v1/...`
- Authentification : JWT Bearer (OAuth2 password flow)
- Réponses : toujours enveloppées `{"data": ..., "meta": {...}}`
- Erreurs : RFC 7807 Problem Details
- Pagination : cursor-based pour les séries temporelles

## Modèle de données clé

### Battery
- `battery_id` : UUID (identifiant interne)
- `asset_id` : identifiant Terna (pour les soumissions MSD)
- `site_id` : regroupement géographique
- `capacity_kwh` : capacité nominale
- `max_power_kw` : puissance max charge/décharge
- `protocol` : `modbus` | `ocpp` | `rest`

### BatteryReading (TimescaleDB hypertable)
- Granularité : 10 secondes
- Rétention : 90 jours full, 2 ans agrégés
- Métriques : `soc_percent`, `power_kw`, `voltage_v`, `temperature_c`, `state`

### DispatchPlan
- Granularité : quarts d'heure (QH) — 96 QH/jour
- Horizon : 24-48h glissant
- Source : `optimizer` | `manual` | `market_signal`

## Marchés électriques italiens — référence rapide

| Marché | Opérateur | Fréquence | Produit | Unité |
|--------|-----------|-----------|---------|-------|
| MGP | GME | Quotidien J-1 | Énergie | MWh/QH |
| MI1-7 | GME | Intra-day | Énergie | MWh/QH |
| MSD ex-ante | Terna | Quotidien J-1 | Réserve/Régulation | MW |
| MB (Mercato Bilanciamento) | Terna | Temps réel | Équilibrage | MW |

**QH** = quart d'heure = unité temporelle de base italienne (≠ demi-heure FR)

## Variables d'environnement critiques

Voir `.env.example` pour la liste complète. Les secrets sont stockés dans AWS Secrets Manager en production, jamais en clair dans le code ou les logs.

## Déploiement

- **Cloud** : AWS eu-south-1 (Milan) — proximité réglementaire et latence
- **Base de données** : RDS PostgreSQL + extension TimescaleDB
- **Conteneurs** : ECS Fargate (API) + Lambda (schedulers market)
- **Message broker** : Amazon MSK (Kafka) pour les lectures batteries temps réel
- **IaC** : Terraform dans `/infra/terraform/`

## Points d'attention

- Les soumissions GME/Terna ont des **deadlines dures** — tout retard entraîne une pénalité. Le scheduler doit avoir des marges de 5 minutes.
- Le **SoC minimum** opérationnel est 10%, maximum 90% (dégradation cycle).
- Les batteries ont des **rampes** (MW/min) à respecter dans les commandes de dispatch.
- En cas de perte de communication avec une batterie, elle passe en mode **safe state** (puissance 0) dans les 30 secondes.
- Toutes les heures sont en **CET/CEST** (Europe/Rome) — attention aux changements d'heure (jours de 23h et 25h).
