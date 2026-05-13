import { useCallback, useEffect, useState } from 'react';
import {
  RefreshCw,
  PowerOff,
  Activity,
  AlertCircle,
  CheckCircle2,
  X,
  CircleArrowDown,
} from 'lucide-react';
import {
  bulkSetBatteryActive,
  deactivateBattery,
  listConfiguredBatteries,
  testBatteryConnection,
} from '../../api/client';
import type { ConfiguredBattery, TestConnectionResponse } from '../../api/types';

function stateColor(state: ConfiguredBattery['state']): string {
  switch (state) {
    case 'charging':
      return 'text-emerald-400 bg-emerald-400/10';
    case 'discharging':
      return 'text-blue-400 bg-blue-400/10';
    case 'idle':
      return 'text-slate-300 bg-slate-500/10';
    case 'fault':
      return 'text-red-400 bg-red-400/10';
    case 'safe_state':
      return 'text-yellow-400 bg-yellow-400/10';
    default:
      return 'text-slate-400 bg-slate-400/10';
  }
}

export function AdminBatteries() {
  const [batteries, setBatteries] = useState<ConfiguredBattery[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [testing, setTesting] = useState<
    Record<string, TestConnectionResponse | 'loading'>
  >({});
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setRefreshing(true);
      setError(null);
      // Management = active batteries only
      const data = await listConfiguredBatteries({ active: true });
      setBatteries(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const handleDeactivate = async (battery: ConfiguredBattery) => {
    if (!window.confirm(
      `Désactiver ${battery.name} ?\n\nLa batterie disparaît du Dashboard et de l'Optimisation, mais reste dans le portefeuille. L'historique est conservé.`
    )) return;
    setBusy(battery.battery_id);
    try {
      await deactivateBattery(battery.battery_id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Désactivation échouée');
    } finally {
      setBusy(null);
    }
  };

  const handleTest = async (battery: ConfiguredBattery) => {
    setTesting((t) => ({ ...t, [battery.battery_id]: 'loading' }));
    try {
      const result = await testBatteryConnection(battery.battery_id);
      setTesting((t) => ({ ...t, [battery.battery_id]: result }));
    } catch (err) {
      setTesting((t) => ({
        ...t,
        [battery.battery_id]: {
          ok: false,
          error: err instanceof Error ? err.message : 'Erreur',
        },
      }));
    }
  };

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Management batterie</h1>
          <p className="text-sm text-slate-400 mt-1">
            Batteries actives dans la VPP — seules celles-ci apparaissent dans Dashboard,
            Optimisation et Activations batteries.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => void refresh()}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-700/50 text-slate-200 hover:bg-slate-700 text-sm transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
            Rafraîchir
          </button>
          <button
            onClick={() => setImportOpen(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-white hover:bg-primary/80 text-sm font-medium transition-colors"
          >
            <CircleArrowDown className="w-4 h-4" />
            Importer du portefeuille
          </button>
        </div>
      </header>

      {error && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-300">
          <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
          <span className="text-sm">{error}</span>
          <button onClick={() => setError(null)} className="ml-auto" aria-label="Fermer">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-12 text-slate-400">
          <Activity className="w-5 h-5 animate-pulse mr-2" />
          Chargement…
        </div>
      ) : batteries.length === 0 ? (
        <div className="text-center py-12 text-slate-400 border-2 border-dashed border-slate-700 rounded-lg">
          Aucune batterie active. Cliquez sur{' '}
          <strong className="text-white">« Importer du portefeuille »</strong> pour activer
          des batteries du catalogue.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-700">
          <table className="w-full text-sm">
            <thead className="bg-slate-800/60 text-slate-300 uppercase text-xs">
              <tr>
                <th className="px-4 py-3 text-left font-medium">Nom</th>
                <th className="px-4 py-3 text-left font-medium">Asset ID</th>
                <th className="px-4 py-3 text-left font-medium">Modèle</th>
                <th className="px-4 py-3 text-right font-medium">Capacité</th>
                <th className="px-4 py-3 text-right font-medium">P max</th>
                <th className="px-4 py-3 text-center font-medium">État</th>
                <th className="px-4 py-3 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700 text-slate-200">
              {batteries.map((b) => {
                const test = testing[b.battery_id];
                return (
                  <tr key={b.battery_id} className="hover:bg-slate-800/40">
                    <td className="px-4 py-3 font-medium text-white">{b.name}</td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">
                      {b.asset_id}
                    </td>
                    <td className="px-4 py-3 text-slate-300">{b.metadata_?.model ?? '—'}</td>
                    <td className="px-4 py-3 text-right text-slate-300">{b.capacity_kwh} kWh</td>
                    <td className="px-4 py-3 text-right text-slate-300">{b.max_power_kw} kW</td>
                    <td className="px-4 py-3 text-center">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${stateColor(
                          b.state
                        )}`}
                      >
                        {b.state}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => void handleTest(b)}
                          className="px-2 py-1 rounded text-xs bg-slate-700/50 hover:bg-slate-700 text-slate-200"
                          title="Tester la connexion"
                        >
                          {test === 'loading' ? (
                            <Activity className="w-3 h-3 animate-spin" />
                          ) : test && typeof test === 'object' && test.ok ? (
                            <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                          ) : test && typeof test === 'object' && !test.ok ? (
                            <AlertCircle className="w-3 h-3 text-red-400" />
                          ) : (
                            'Test'
                          )}
                        </button>
                        <button
                          onClick={() => void handleDeactivate(b)}
                          disabled={busy === b.battery_id}
                          className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-yellow-500/10 text-yellow-400 hover:bg-yellow-500/20 disabled:opacity-50"
                          title="Désactiver"
                        >
                          <PowerOff className="w-3 h-3" />
                          Désactiver
                        </button>
                      </div>
                      {test && typeof test === 'object' && (
                        <div className="text-xs text-slate-400 mt-1">
                          {test.ok
                            ? `SoC ${test.soc_percent}% · ${test.power_kw} kW · ${test.temperature_c}°C`
                            : `Erreur : ${test.error}`}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {importOpen && (
        <ImportFromPortfolioModal
          onClose={() => setImportOpen(false)}
          onActivated={() => {
            setImportOpen(false);
            void refresh();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ImportFromPortfolioModal — picks inactive batteries from the portfolio,
// activates them in bulk.
// ---------------------------------------------------------------------------

interface ImportModalProps {
  onClose: () => void;
  onActivated: () => void;
}

function ImportFromPortfolioModal({ onClose, onActivated }: ImportModalProps) {
  const [inactiveBatteries, setInactiveBatteries] = useState<ConfiguredBattery[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [activating, setActivating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const data = await listConfiguredBatteries({ active: false });
        setInactiveBatteries(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Chargement échoué');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const toggle = (id: string) => {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === inactiveBatteries.length) setSelected(new Set());
    else setSelected(new Set(inactiveBatteries.map((b) => b.battery_id)));
  };

  const handleActivate = async () => {
    if (selected.size === 0) return;
    setActivating(true);
    setError(null);
    try {
      const activated = await bulkSetBatteryActive(Array.from(selected), true);
      setResult(`${activated.length} batterie(s) activée(s).`);
      setTimeout(onActivated, 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Activation échouée');
    } finally {
      setActivating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="bg-surface rounded-xl border border-slate-700 max-w-3xl w-full max-h-[90vh] overflow-hidden flex flex-col">
        <header className="flex items-center justify-between p-4 border-b border-slate-700">
          <div>
            <h2 className="text-lg font-bold text-white">Activer des batteries du portefeuille</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Les batteries activées apparaissent immédiatement dans Dashboard, Optimisation et
              Activations.
            </p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="w-5 h-5" />
          </button>
        </header>

        <div className="p-4 space-y-3 overflow-y-auto flex-1">
          {error && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-300 text-sm">
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              {error}
            </div>
          )}

          {result && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-sm">
              <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" />
              {result}
            </div>
          )}

          {loading ? (
            <div className="py-8 text-center text-slate-400">
              <Activity className="w-5 h-5 animate-pulse inline mr-2" />
              Chargement du portefeuille…
            </div>
          ) : inactiveBatteries.length === 0 ? (
            <div className="text-center py-8 text-slate-400 border-2 border-dashed border-slate-700 rounded-lg text-sm">
              Aucune batterie inactive dans le portefeuille — toutes sont déjà actives ou le
              portefeuille est vide.
              <br />
              <span className="text-xs text-slate-500">
                Va dans « Portefeuille batteries » pour en créer ou en importer.
              </span>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm text-slate-400">
                  {inactiveBatteries.length} batterie(s) inactive(s) — {selected.size}{' '}
                  sélectionnée(s)
                </p>
                <button onClick={toggleAll} className="text-xs text-primary hover:underline">
                  {selected.size === inactiveBatteries.length
                    ? 'Tout désélectionner'
                    : 'Tout sélectionner'}
                </button>
              </div>
              <div className="overflow-x-auto rounded-lg border border-slate-700">
                <table className="w-full text-sm">
                  <thead className="bg-slate-800/60 text-slate-300 uppercase text-xs">
                    <tr>
                      <th className="px-3 py-2 w-8"></th>
                      <th className="px-3 py-2 text-left font-medium">Nom</th>
                      <th className="px-3 py-2 text-left font-medium">Asset ID</th>
                      <th className="px-3 py-2 text-left font-medium">Modèle</th>
                      <th className="px-3 py-2 text-right font-medium">kWh</th>
                      <th className="px-3 py-2 text-right font-medium">kW</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700 text-slate-200">
                    {inactiveBatteries.map((b) => (
                      <tr
                        key={b.battery_id}
                        className="hover:bg-slate-800/40 cursor-pointer"
                        onClick={() => toggle(b.battery_id)}
                      >
                        <td className="px-3 py-2">
                          <input
                            type="checkbox"
                            checked={selected.has(b.battery_id)}
                            onChange={() => toggle(b.battery_id)}
                            className="rounded"
                            onClick={(e) => e.stopPropagation()}
                          />
                        </td>
                        <td className="px-3 py-2 font-medium text-white">{b.name}</td>
                        <td className="px-3 py-2 font-mono text-xs">{b.asset_id}</td>
                        <td className="px-3 py-2">{b.metadata_?.model ?? '—'}</td>
                        <td className="px-3 py-2 text-right">{b.capacity_kwh}</td>
                        <td className="px-3 py-2 text-right">{b.max_power_kw}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        <footer className="flex justify-end gap-2 p-4 border-t border-slate-700">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg bg-slate-700/50 text-slate-200 hover:bg-slate-700 text-sm"
          >
            Annuler
          </button>
          <button
            onClick={() => void handleActivate()}
            disabled={activating || selected.size === 0}
            className="px-4 py-2 rounded-lg bg-success text-white hover:bg-success/80 disabled:opacity-50 text-sm font-medium"
          >
            {activating ? 'Activation…' : `Activer ${selected.size} batterie(s)`}
          </button>
        </footer>
      </div>
    </div>
  );
}
