import { useState, useCallback, useEffect } from 'react';
import { Zap, AlertTriangle, CheckCircle, Loader2, X, ToggleLeft, ToggleRight } from 'lucide-react';
import { ScheduleChart } from '../components/ScheduleChart';
import {
  runOptimizeAutoconsommation,
  runOptimizeArbitrage,
  runOptimizeStochastique,
  applyDispatch,
  listConfiguredBatteries,
  fetchMGPPrices,
} from '../api/client';
import type { OptimizeResult, ArbitrageMode } from '../api/types';

interface SiteOption {
  id: string;
  label: string;
}

export function Optimize() {
  const [scenario, setScenario] = useState<'autoconsommation' | 'arbitrage' | 'stochastique'>('arbitrage');
  const [sites, setSites] = useState<SiteOption[]>([]);
  const [siteId, setSiteId] = useState<string>('');
  const [mgpPrices, setMgpPrices] = useState<number[]>([]);
  const [arbitrageMode, setArbitrageMode] = useState<ArbitrageMode>('standard');
  const [incertitudePct, setIncertitudePct] = useState(20);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OptimizeResult | null>(null);
  const [showCompare, setShowCompare] = useState(false);
  const [showConfirmModal, setShowConfirmModal] = useState(false);
  const [applyLoading, setApplyLoading] = useState(false);
  const [applySuccess, setApplySuccess] = useState<string | null>(null);

  // Load real sites (distinct site_id from batteries) and MGP prices on mount.
  useEffect(() => {
    void (async () => {
      try {
        const bats = await listConfiguredBatteries();
        const grouped = new Map<string, string>();
        for (const b of bats) {
          if (!grouped.has(b.site_id)) {
            const plant = b.metadata_?.plant_code ?? b.name;
            grouped.set(b.site_id, plant);
          }
        }
        const list: SiteOption[] = Array.from(grouped.entries()).map(([id, label]) => ({
          id,
          label,
        }));
        setSites(list);
        if (list.length > 0) setSiteId(list[0].id);
      } catch {
        // ignore — page still usable, user can re-try after seeding batteries
      }
      try {
        const mgp = await fetchMGPPrices();
        setMgpPrices(mgp.prices.map((p) => p.price_eur_mwh));
      } catch {
        // fallback to a default curve if /markets/mgp/prices fails
        setMgpPrices([45, 42, 40, 38, 37, 38, 50, 75, 90, 85, 78, 72, 68, 65, 70, 80, 95, 110, 105, 92, 78, 65, 55, 48]);
      }
    })();
  }, []);

  const currentSchedule = Array.from({ length: 24 }, (_, i) => ({
    hour: i,
    power_kw: i >= 8 && i <= 20 ? 80 + Math.sin(i) * 50 : -60,
  }));

  const handleOptimize = useCallback(async () => {
    if (!siteId) {
      setError('Aucun site disponible : importe d\'abord des batteries dans Admin Batteries.');
      return;
    }
    setLoading(true); setError(null); setResult(null);
    try {
      let res: OptimizeResult;
      if (scenario === 'autoconsommation') {
        res = await runOptimizeAutoconsommation({
          site_id: siteId,
          production_pv_kw: Array.from({ length: 24 }, (_, i) => i >= 6 && i <= 19 ? 200 * Math.sin(((i - 6) / 13) * Math.PI) : 0),
          consommation_kw: Array.from({ length: 24 }, (_, i) => 80 + (i >= 7 && i <= 22 ? 60 : 0)),
          prix_mgp: mgpPrices,
        });
      } else if (scenario === 'arbitrage') {
        res = await runOptimizeArbitrage({ site_id: siteId, prix_mgp: mgpPrices, mode: arbitrageMode });
      } else {
        res = await runOptimizeStochastique({ site_id: siteId, prix_mgp_base: mgpPrices, incertitude_pct: incertitudePct });
      }
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur d'optimisation");
    } finally {
      setLoading(false);
    }
  }, [scenario, siteId, arbitrageMode, incertitudePct, mgpPrices]);

  const handleApply = async () => {
    if (!result) return;
    setApplyLoading(true);
    try {
      const res = await applyDispatch({ schedule: result.schedule });
      setApplySuccess(res.message);
      setShowConfirmModal(false);
    } catch {
      setApplySuccess("Erreur lors de l'application");
    } finally {
      setApplyLoading(false);
      setTimeout(() => setApplySuccess(null), 5000);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-white">Optimisation</h1>
        <p className="text-sm text-slate-400 mt-0.5">Calcul du planning de dispatch optimal</p>
      </div>
      {applySuccess && (
        <div role="status" className="flex items-center gap-2 p-3 rounded-lg bg-success/10 border border-success/30 text-success text-sm">
          <CheckCircle className="w-4 h-4 flex-shrink-0" />{applySuccess}
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        <aside className="lg:col-span-2 bg-surface rounded-xl border border-border p-5 space-y-5">
          <h2 className="text-sm font-semibold text-slate-300">Parametres</h2>
          <div className="space-y-1.5">
            <label htmlFor="scenario" className="block text-xs font-medium text-slate-400">Scenario</label>
            <select id="scenario" value={scenario} onChange={(e) => setScenario(e.target.value as typeof scenario)}
              className="w-full px-3 py-2 rounded-lg bg-background border border-border text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary">
              <option value="autoconsommation">Autoconsommation PV</option>
              <option value="arbitrage">Arbitrage MGP</option>
              <option value="stochastique">Stochastique</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <label htmlFor="site" className="block text-xs font-medium text-slate-400">Site</label>
            <select id="site" value={siteId} onChange={(e) => setSiteId(e.target.value)}
              className="w-full px-3 py-2 rounded-lg bg-background border border-border text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary">
              {sites.length === 0 && <option value="">Aucun site (importer batteries)</option>}
              {sites.map((s) => <option key={s.id} value={s.id}>{s.label} ({s.id.slice(0, 8)}…)</option>)}
            </select>
          </div>
          {scenario === 'arbitrage' && (
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-slate-400">Mode arbitrage</p>
              <div className="grid grid-cols-3 gap-1.5" role="radiogroup">
                {(['conservateur', 'standard', 'agressif'] as ArbitrageMode[]).map((m) => (
                  <button key={m} role="radio" aria-checked={arbitrageMode === m} onClick={() => setArbitrageMode(m)}
                    className={`px-2 py-2 rounded-lg text-xs font-medium transition-colors capitalize border
                      ${arbitrageMode === m ? 'bg-primary/20 text-primary border-primary/40' : 'bg-background text-slate-400 border-border hover:border-slate-500'}`}>
                    {m}
                  </button>
                ))}
              </div>
            </div>
          )}
          {scenario === 'stochastique' && (
            <div className="space-y-2">
              <div className="flex justify-between text-xs">
                <label htmlFor="incertitude" className="text-slate-400 font-medium">Incertitude prix</label>
                <span className="text-white font-bold">{incertitudePct}%</span>
              </div>
              <input id="incertitude" type="range" min={10} max={40} step={1} value={incertitudePct}
                onChange={(e) => setIncertitudePct(parseInt(e.target.value, 10))}
                className="w-full h-2 rounded-lg appearance-none cursor-pointer bg-slate-700 accent-primary" />
              <div className="flex justify-between text-xs text-slate-500"><span>10%</span><span>40%</span></div>
            </div>
          )}
          <button onClick={() => { void handleOptimize(); }} disabled={loading}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-primary text-white font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            aria-busy={loading}>
            {loading ? <><Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />Optimisation en cours...</> : <><Zap className="w-4 h-4" aria-hidden="true" />Lancer l'optimisation</>}
          </button>
          {error && <div role="alert" className="flex items-center gap-2 text-danger text-xs"><AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />{error}</div>}
        </aside>
        <div className="lg:col-span-3 space-y-4">
          <div className="bg-surface rounded-xl border border-border p-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-slate-300">Planning 24h</h2>
              {result && (
                <button onClick={() => setShowCompare((p) => !p)}
                  className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors border
                    ${showCompare ? 'bg-warning/20 text-warning border-warning/40' : 'bg-slate-700 text-slate-400 border-border hover:border-slate-500'}`}
                  aria-pressed={showCompare}>
                  {showCompare ? <ToggleRight className="w-3.5 h-3.5" /> : <ToggleLeft className="w-3.5 h-3.5" />}
                  IA vs Actuel
                </button>
              )}
            </div>
            <ScheduleChart schedule={result?.schedule ?? []} compareSchedule={currentSchedule} showCompare={showCompare} />
          </div>
          {result && (
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-surface rounded-xl border border-border p-4">
                <p className="text-xs text-slate-400 mb-1">Revenus estimes</p>
                <p className="text-xl font-bold text-warning">{result.revenus_estimes_eur.toLocaleString('fr-FR', { maximumFractionDigits: 0 })} €</p>
              </div>
              {result.taux_autoconsommation_pct != null && (
                <div className="bg-surface rounded-xl border border-border p-4">
                  <p className="text-xs text-slate-400 mb-1">Taux autoconsommation</p>
                  <p className="text-xl font-bold text-success">{result.taux_autoconsommation_pct.toFixed(1)}%</p>
                </div>
              )}
              {result.sharpe_ratio != null && (
                <div className="bg-surface rounded-xl border border-border p-4">
                  <p className="text-xs text-slate-400 mb-1">Sharpe Ratio</p>
                  <p className="text-xl font-bold text-primary">{result.sharpe_ratio.toFixed(2)}</p>
                </div>
              )}
              {result.cvar != null && (
                <div className="bg-surface rounded-xl border border-border p-4">
                  <p className="text-xs text-slate-400 mb-1">CVaR (perte max 95%)</p>
                  <p className="text-xl font-bold text-danger">{result.cvar.toFixed(0)} €</p>
                </div>
              )}
            </div>
          )}
          {result && (
            <button onClick={() => setShowConfirmModal(true)}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-success text-white font-medium hover:bg-success/90 transition-colors">
              <CheckCircle className="w-4 h-4" aria-hidden="true" />Appliquer ce planning
            </button>
          )}
        </div>
      </div>
      {showConfirmModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
          <div className="bg-surface rounded-2xl border border-border p-6 max-w-sm w-full space-y-4 shadow-2xl">
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-warning/20"><AlertTriangle className="w-5 h-5 text-warning" aria-hidden="true" /></div>
                <h3 id="confirm-title" className="font-semibold text-white">Confirmer l'application</h3>
              </div>
              <button onClick={() => setShowConfirmModal(false)} className="text-slate-400 hover:text-white" aria-label="Annuler"><X className="w-5 h-5" /></button>
            </div>
            <p className="text-sm text-slate-400">Ce planning sera envoye aux batteries du site <strong className="text-white">{siteId}</strong>. Cette action affectera le dispatch en temps reel.</p>
            <div className="flex gap-3">
              <button onClick={() => setShowConfirmModal(false)} className="flex-1 px-4 py-2 rounded-lg bg-slate-700 text-slate-300 text-sm font-medium hover:bg-slate-600 transition-colors">Annuler</button>
              <button onClick={() => { void handleApply(); }} disabled={applyLoading}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-success text-white text-sm font-medium hover:bg-success/90 disabled:opacity-50 transition-colors"
                aria-busy={applyLoading}>
                {applyLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}Confirmer
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
