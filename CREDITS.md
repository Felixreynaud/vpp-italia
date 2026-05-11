# Credits et licences open source

Ce projet integre ou s'inspire des projets open source suivants.

---

## vinerya/virtual-power-plant

- **Auteurs** : Moudather Chelbi & Mariem Khemir
- **Depot** : https://github.com/vinerya/virtual-power-plant
- **Licence** : MIT

### Composants integres dans vpp-italia

| Composant vpp-italia | Source originale |
|---|---|
| `core/optimization/stochastic.py` | `src/vpp/optimization/stochastic.py` — architecture ScenarioGenerator & ScenarioSet |
| `core/optimization/arbitrage.py` | `src/vpp/trading/strategies.py` — concepts CVaR et Sharpe ratio |
| `core/optimization/scenarios.py` | `benchmarks/scenarios.py` — structure Scenario, ScenarioRegistry |
| `core/optimization/peak_shaving.py` | `src/vpp/optimization/` — pattern d'optimisation regle-metier |

Les algorithmes ont ete reecrits et adaptes pour :
- Le marche electrique italien (MGP, MSD, MB, Terna)
- Les batteries LUNA2000 (contraintes SoC 10-90%, puissance max 108 kW)
- L'API FastAPI asynchrone de vpp-italia
- L'integration TimescaleDB / PostgreSQL

### Texte de licence MIT (vinerya/virtual-power-plant)

```
MIT License

Copyright (c) 2024 Moudather Chelbi & Mariem Khemir

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## jasonacox/Powerwall-Dashboard

- **Auteur** : Jason Cox
- **Depot** : https://github.com/jasonacox/Powerwall-Dashboard
- **Licence** : MIT

### Composants integres dans vpp-italia

| Composant vpp-italia | Inspiration |
|---|---|
| `monitoring/grafana/dashboards/vpp_main.json` | Structure generale du dashboard (rows, panels layout, state-timeline) |
| `monitoring/grafana/dashboards/dashboard_fleet.json` | Heatmap SoC, piechart repartition etats |
| `frontend/src/components/EnergyFlow.tsx` | Animation flux d'energie batterie <-> reseau |

Le code des dashboards Grafana a ete entierement reecrit pour :
- Remplacer InfluxDB -> PostgreSQL/TimescaleDB (sources de donnees)
- Adapter les metriques au modele de donnees vpp-italia (battery_readings, dispatch_plans)
- Ajouter les specificites italiennes (MSD, GME, Terna, CET/CEST)

### Texte de licence MIT (jasonacox/Powerwall-Dashboard)

```
MIT License

Copyright (c) 2021-2024 Jason Cox

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Autres dependances open source notables

| Bibliotheque | Licence | Usage |
|---|---|---|
| FastAPI | MIT | Framework API REST |
| PuLP | MIT | Solveur LP/MILP (optimizer.py) |
| SQLAlchemy | MIT | ORM PostgreSQL/TimescaleDB |
| React | MIT | Interface web frontend |
| Recharts | MIT | Graphiques frontend |
| Tailwind CSS | MIT | Styles frontend |
| Grafana | AGPL-3.0 | Dashboards monitoring |
| TimescaleDB | Apache-2.0 | Extension PostgreSQL series temporelles |
