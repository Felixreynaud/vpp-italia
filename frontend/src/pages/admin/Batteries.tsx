import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  RefreshCw,
  Plus,
  Trash2,
  Activity,
  AlertCircle,
  CheckCircle2,
  X,
  Search,
} from 'lucide-react';
import {
  bulkImportBatteries,
  deleteBattery,
  discoverHuawei,
  listConfiguredBatteries,
  testBatteryConnection,
} from '../../api/client';
import type {
  BulkImportItem,
  ConfiguredBattery,
  DiscoveredBattery,
  TestConnectionResponse,
} from '../../api/types';

const DEFAULT_ENDPOINT = 'http://127.0.0.1:9999';
const DEFAULT_CLIENT_ID = 'sim';
const DEFAULT_CLIENT_SECRET = 'sim';

// Italian region codes used to derive a stable site_id from a plant_code.
function siteIdFromPlantCode(plantCode: string): string {
  // We need a UUID — derive deterministically from plant_code prefix.
  const region = plantCode.split('-')[1] ?? 'XX';
  return `00000000-0000-0000-0000-0000000000${region.charCodeAt(0).toString(16).padStart(2, '0')}`;
}

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
  const [discoverOpen, setDiscoverOpen] = useState(false);
  const [testing, setTesting] = useState<Record<string, TestConnectionResponse | 'loading'>>({});

  const refresh = useCallback(async () => {
    try {
      setRefreshing(true);
      setError(null);
      const data = await listConfiguredBatteries();
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

  const handleDelete = async (battery: ConfiguredBattery) => {
    if (!window.confirm(`Supprimer ${battery.name} de la flotte ?`)) return;
    try {
      await deleteBattery(battery.battery_id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Suppression échouée');
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
          <h1 className="text-2xl font-bold text-white">Admin — Parc Batteries</h1>
          <p className="text-sm text-slate-400 mt-1">
            Découverte, import et gestion des batteries connectées à la VPP.
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
            onClick={() => setDiscoverOpen(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-white hover:bg-primary/80 text-sm font-medium transition-colors"
          >
            <Plus className="w-4 h-4" />
            Découvrir & importer
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
          Aucune batterie configurée. Cliquez sur <strong className="text-white">« Découvrir & importer »</strong> pour en ajouter.
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
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">{b.asset_id}</td>
                    <td className="px-4 py-3 text-slate-300">{b.metadata_?.model ?? '—'}</td>
                    <td className="px-4 py-3 text-right text-slate-300">{b.capacity_kwh} kWh</td>
                    <td className="px-4 py-3 text-right text-slate-300">{b.max_power_kw} kW</td>
                    <td className="px-4 py-3 text-center">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${stateColor(b.state)}`}
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
                          onClick={() => void handleDelete(b)}
                          className="p-1.5 rounded text-red-400 hover:bg-red-400/10"
                          title="Supprimer"
                        >
                          <Trash2 className="w-4 h-4" />
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

      {discoverOpen && (
        <DiscoverModal
          onClose={() => setDiscoverOpen(false)}
          onImported={() => {
            setDiscoverOpen(false);
            void refresh();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// DiscoverModal — inline because it's only used here.
// ---------------------------------------------------------------------------

interface DiscoverModalProps {
  onClose: () => void;
  onImported: () => void;
}

function DiscoverModal({ onClose, onImported }: DiscoverModalProps) {
  const [endpointUrl, setEndpointUrl] = useState(DEFAULT_ENDPOINT);
  const [clientId, setClientId] = useState(DEFAULT_CLIENT_ID);
  const [clientSecret, setClientSecret] = useState(DEFAULT_CLIENT_SECRET);

  const [scanning, setScanning] = useState(false);
  const [discovered, setDiscovered] = useState<DiscoveredBattery[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<string | null>(null);

  const handleScan = async () => {
    setError(null);
    setImportResult(null);
    setScanning(true);
    try {
      const res = await discoverHuawei(endpointUrl, clientId, clientSecret);
      setDiscovered(res.data);
      // Pre-select all by default
      setSelected(new Set(res.data.map((d) => d.device_id)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Échec du scan');
      setDiscovered([]);
    } finally {
      setScanning(false);
    }
  };

  const toggle = (deviceId: string) => {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(deviceId)) next.delete(deviceId);
      else next.add(deviceId);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === discovered.length) setSelected(new Set());
    else setSelected(new Set(discovered.map((d) => d.device_id)));
  };

  const itemsToImport: BulkImportItem[] = useMemo(
    () =>
      discovered
        .filter((d) => selected.has(d.device_id))
        .map((d, i) => ({
          asset_id: `UPCA-IT-${d.plant_code.replace(/[^A-Z0-9]/g, '')}-${i + 1}`,
          site_id: siteIdFromPlantCode(d.plant_code),
          name: `${d.plant_name}`,
          plant_code: d.plant_code,
          device_id: d.device_id,
          model: d.model,
          capacity_kwh: d.capacity_kwh,
          max_power_kw: d.max_power_kw,
        })),
    [discovered, selected]
  );

  const handleImport = async () => {
    setError(null);
    setImporting(true);
    try {
      const res = await bulkImportBatteries(
        endpointUrl,
        clientId,
        clientSecret,
        itemsToImport
      );
      setImportResult(`${res.imported} importée(s), ${res.skipped} ignorée(s) (déjà existante).`);
      // Wait 1.5s then close so user sees the success message
      setTimeout(() => onImported(), 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import échoué');
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="bg-surface rounded-xl border border-slate-700 max-w-3xl w-full max-h-[90vh] overflow-hidden flex flex-col">
        <header className="flex items-center justify-between p-4 border-b border-slate-700">
          <h2 className="text-lg font-bold text-white">Découvrir des batteries Huawei FusionSolar</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="w-5 h-5" />
          </button>
        </header>

        <div className="p-4 space-y-4 overflow-y-auto flex-1">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="md:col-span-3">
              <label className="block text-xs font-medium text-slate-400 mb-1">
                Endpoint URL
              </label>
              <input
                type="text"
                value={endpointUrl}
                onChange={(e) => setEndpointUrl(e.target.value)}
                placeholder="http://127.0.0.1:9999 (simulator) ou https://intl.fusionsolar.huawei.com"
                className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Client ID</label>
              <input
                type="text"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Client secret</label>
              <input
                type="password"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:outline-none focus:border-primary"
              />
            </div>
            <div className="flex items-end">
              <button
                onClick={() => void handleScan()}
                disabled={scanning}
                className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary text-white hover:bg-primary/80 disabled:opacity-50 text-sm font-medium"
              >
                <Search className={`w-4 h-4 ${scanning ? 'animate-pulse' : ''}`} />
                {scanning ? 'Scan en cours…' : 'Scanner'}
              </button>
            </div>
          </div>

          {error && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-300 text-sm">
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              {error}
            </div>
          )}

          {importResult && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-sm">
              <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" />
              {importResult}
            </div>
          )}

          {discovered.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm text-slate-400">
                  {discovered.length} batterie(s) trouvée(s) — {selected.size} sélectionnée(s)
                </p>
                <button
                  onClick={toggleAll}
                  className="text-xs text-primary hover:underline"
                >
                  {selected.size === discovered.length ? 'Tout désélectionner' : 'Tout sélectionner'}
                </button>
              </div>
              <div className="overflow-x-auto rounded-lg border border-slate-700">
                <table className="w-full text-sm">
                  <thead className="bg-slate-800/60 text-slate-300 uppercase text-xs">
                    <tr>
                      <th className="px-3 py-2 w-8"></th>
                      <th className="px-3 py-2 text-left font-medium">Plant</th>
                      <th className="px-3 py-2 text-left font-medium">Modèle</th>
                      <th className="px-3 py-2 text-right font-medium">kWh</th>
                      <th className="px-3 py-2 text-right font-medium">kW</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700 text-slate-200">
                    {discovered.map((d) => (
                      <tr key={d.device_id} className="hover:bg-slate-800/40">
                        <td className="px-3 py-2">
                          <input
                            type="checkbox"
                            checked={selected.has(d.device_id)}
                            onChange={() => toggle(d.device_id)}
                            className="rounded"
                          />
                        </td>
                        <td className="px-3 py-2 font-mono text-xs">{d.plant_code}</td>
                        <td className="px-3 py-2">{d.model ?? '—'}</td>
                        <td className="px-3 py-2 text-right">{d.capacity_kwh}</td>
                        <td className="px-3 py-2 text-right">{d.max_power_kw}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
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
            onClick={() => void handleImport()}
            disabled={importing || itemsToImport.length === 0}
            className="px-4 py-2 rounded-lg bg-primary text-white hover:bg-primary/80 disabled:opacity-50 text-sm font-medium"
          >
            {importing ? 'Import…' : `Importer ${itemsToImport.length} batterie(s)`}
          </button>
        </footer>
      </div>
    </div>
  );
}
