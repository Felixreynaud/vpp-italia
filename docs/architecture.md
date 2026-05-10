# Architecture Decision Records

## ADR-001 — TimescaleDB pour les séries temporelles batteries

**Contexte** : Les lectures de batteries (10 secondes × 100+ batteries) génèrent ~10 000 points/minute, soit ~14 millions/jour.

**Décision** : TimescaleDB (extension PostgreSQL) plutôt qu'InfluxDB ou Prometheus remote write.

**Raisons** :
- Requêtes SQL standard pour toute la couche data (uniformité)
- `time_bucket()` pour les agrégats QH nativement
- Compression automatique (ratio ~20x sur données numériques)
- Politique de rétention intégrée (90 jours full, puis agrégats)
- RDS PostgreSQL disponible sur eu-south-1

**Compromis** : Performance en insertion légèrement inférieure à InfluxDB, mais suffisante à notre charge.

---

## ADR-002 — Région AWS eu-south-1 (Milan)

**Contexte** : Réglementation GDPR et latence réseau vers GME/Terna.

**Décision** : eu-south-1 (Milan) comme région principale.

**Raisons** :
- Données clients et mesures restent en Italie (conformité réglementaire)
- Latence < 10ms vers les API GME et Terna (hébergées en Italie)
- Pas de transfert de données inter-régions pour les soumissions de marché

**Compromis** : Moins de services AWS disponibles qu'en eu-west-1 (pas de certains services managés).

---

## ADR-003 — Optimiseur LP (PuLP/CBC) vs MILP commercial

**Contexte** : Choix du solveur pour l'optimisation dispatch 24h × 100 batteries.

**Décision** : PuLP avec solveur CBC par défaut, interface pour Gurobi en production si nécessaire.

**Raisons** :
- CBC gratuit et open-source, performances suffisantes pour problèmes < 10 000 variables
- PuLP supporte CBC, GLPK, Gurobi, CPLEX via la même interface
- Horizon 24h × 96 QH × 100 batteries = problème LP de ~9 600 variables — solvable en < 10s

**Compromis** : Pour des contraintes MILP complexes (engagement/désengagement), Gurobi est ~10x plus rapide.

---

## ADR-004 — Quart-d'heure (QH) comme unité temporelle

**Contexte** : Choix de la granularité des plans de dispatch et des offres de marché.

**Décision** : 15 minutes (96 QH/jour) aligné sur le marché italien.

**Raisons** :
- GME et Terna utilisent le QH comme unité de base (≠ demi-heure du marché français)
- Granularité suffisante pour la flexibilité batterie sans explosion de la complexité LP
- Cohérence avec les index TimescaleDB (`time_bucket('15 minutes', time)`)

---

## ADR-005 — Multi-protocoles (Modbus, OCPP, REST)

**Contexte** : Le parc de 100+ batteries inclut des équipements de différents fabricants.

**Décision** : Abstraction `connector` par protocole, avec une interface commune `set_power_kw() / read_telemetry()`.

**Raisons** :
- Batteries industrielles (>500 kWh) : Modbus TCP dominant
- Bornes de stockage bi-directionnelles V2G : OCPP 2.0.1
- Fabricants avec API propriétaires : connecteur REST générique
- Interface commune permet de changer de protocole sans modifier le core

---

## Flux de données temps réel

```
Batteries (10s)
    │
    ▼
Connectors (Modbus/OCPP/REST)
    │
    ▼
Kafka topic: battery.readings
    │
    ├──▶ TimescaleDB (persistence)
    │
    ├──▶ Core: watchdog safe-state
    │
    └──▶ Monitoring: métriques Prometheus
```

## Flux de dispatch

```
MarketScheduler (cron-like)
    │ (J-1 avant 11:55 CET)
    ▼
Optimizer (LP 24h)
    │
    ▼
DispatchPlan rows en DB
    │ (début de chaque QH)
    ▼
DispatchExecutor → Connectors → Batteries
    │
    ▼
CommandResult → Kafka topic: dispatch.commands
```
