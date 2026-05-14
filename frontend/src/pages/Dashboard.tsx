import { useState } from 'react';
import { Battery, Zap, Activity, TrendingUp, Euro, RefreshCw, ToggleLeft, ToggleRight } from 'lucide-react';
import { KpiCard } from '../components/KpiCard';
import { EnergyFlow } from '../components/EnergyFlow';
import { PriceChart } from '../components/PriceChart';
import { useFleet } from '../hooks/useFleet';

const MGP_ZONES: { code: string; label: string }[] = [
  { code: 'NORD', label: 'Nord' },
  { code: 'CNOR', label: 'Centre-Nord' },
  { code: 'CSUD', label: 'Centre-Sud' },
  { code: 'SUD', label: 'Sud' },
  { code: 'CALA', label: 'Calabre' },
  { code: 'SARD', label: 'Sardaigne' },
  { code: 'SICI', label: 'Sicile' },
  { code: 'PUN', label: 'PUN (national)' },
];

export function Dashboard() {
  const [zone, setZone] = useState<string>('NORD');
  const { metrics, mgpPrices, loading, error, refresh } = useFleet(10000, zone);
  const [autoMode, setAutoMode] = useState(true);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Dashboard</h1>
          <p className="text-sm text-slate-400 mt-0.5">Vue temps reel du parc VPP</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setAutoMode((prev) => !prev)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors border
              ${autoMode ? 'bg-primary/20 text-primary border-primary/40 hover:bg-primary/30' : 'bg-slate-700 text-slate-300 border-border hover:bg-slate-600'}`}
            aria-pressed={autoMode}
          >
            {autoMode ? <ToggleRight className="w-4 h-4" aria-hidden="true" /> : <ToggleLeft className="w-4 h-4" aria-hidden="true" />}
            {autoMode ? 'Mode Auto' : 'Mode Manuel'}
          </button>
          <button onClick={refresh} className="p-2 rounded-lg bg-surface border border-border text-slate-400 hover:text-white hover:border-slate-500 transition-colors" aria-label="Actualiser les donnees">
            <RefreshCw className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>
      </div>
      {error && <div role="alert" className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">Erreur de connexion: {error}</div>}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <KpiCard label="SoC Moyen" value={metrics ? metrics.soc_moyen.toFixed(1) : '—'} unit="%" icon={Battery}
          color={metrics ? metrics.soc_moyen < 20 ? 'danger' : metrics.soc_moyen > 85 ? 'warning' : 'success' : 'primary'} loading={loading} />
        <KpiCard label="Puissance Totale" value={metrics ? metrics.puissance_totale_kw.toFixed(0) : '—'} unit="kW" icon={Zap}
          color={metrics && metrics.puissance_totale_kw > 0 ? 'success' : 'primary'} loading={loading} />
        <KpiCard label="Batteries Actives" value={metrics ? `${metrics.batteries_actives}/${metrics.batteries_total}` : '—'} unit="" icon={Activity} color="primary" loading={loading} />
        <KpiCard label="Energie Disponible" value={metrics ? metrics.energie_disponible_mwh.toFixed(1) : '—'} unit="MWh" icon={TrendingUp} color="success" loading={loading} />
        <KpiCard label="P&L du Jour" value={metrics?.pnl_jour_eur != null ? metrics.pnl_jour_eur.toFixed(0) : '—'} unit="€" icon={Euro} color="warning" trend="up" trendValue="+12% vs hier" loading={loading} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-surface rounded-xl border border-border p-5">
          <h2 className="text-sm font-semibold text-slate-300 mb-4">Flux d'Energie</h2>
          {loading ? <div className="h-44 flex items-center justify-center"><div className="skeleton h-40 w-full rounded-xl" /></div> : <EnergyFlow metrics={metrics} />}
          <div className="mt-3 flex items-center justify-center gap-2 text-xs text-slate-400">
            <div className={`w-2 h-2 rounded-full ${autoMode ? 'bg-success animate-pulse' : 'bg-warning'}`} aria-hidden="true" />
            {autoMode ? 'Dispatch automatique actif' : 'Controle manuel — dispatch suspendu'}
          </div>
        </div>
        <div className="bg-surface rounded-xl border border-border p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-slate-300">Prix MGP — 24h</h2>
            <div className="flex items-center gap-3">
              <select
                value={zone}
                onChange={(e) => setZone(e.target.value)}
                aria-label="Zone MGP"
                className="bg-background border border-border rounded px-2 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-primary"
              >
                {MGP_ZONES.map((z) => (
                  <option key={z.code} value={z.code}>
                    {z.label}
                  </option>
                ))}
              </select>
              <span className="text-xs text-slate-500">€/MWh</span>
            </div>
          </div>
          {loading ? <div className="skeleton h-40 w-full rounded-xl" /> : <PriceChart prices={mgpPrices} />}
          {mgpPrices.length > 0 && (
            <div className="mt-2 grid grid-cols-3 gap-2">
              {[
                { label: 'Min', value: Math.min(...mgpPrices.map((p) => p.price_eur_mwh)), color: 'text-success' },
                { label: 'Moy', value: mgpPrices.reduce((s, p) => s + p.price_eur_mwh, 0) / mgpPrices.length, color: 'text-primary' },
                { label: 'Max', value: Math.max(...mgpPrices.map((p) => p.price_eur_mwh)), color: 'text-danger' },
              ].map(({ label, value, color }) => (
                <div key={label} className="text-center">
                  <p className="text-xs text-slate-500">{label}</p>
                  <p className={`text-sm font-bold ${color}`}>{value.toFixed(1)}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <p className="text-xs text-slate-600 text-right">Actualisation automatique toutes les 10s</p>
    </div>
  );
}
