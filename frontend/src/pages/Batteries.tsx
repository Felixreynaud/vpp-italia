import { useState, useMemo } from 'react';
import { Search, AlertTriangle, RefreshCw } from 'lucide-react';
import { BatteryCard } from '../components/BatteryCard';
import { useBatteries } from '../hooks/useBatteries';
import type { BatteryState } from '../api/types';

const STATES: { value: BatteryState | 'all'; label: string }[] = [
  { value: 'all', label: 'Tous' },
  { value: 'idle', label: 'Inactif' },
  { value: 'charging', label: 'Charge' },
  { value: 'discharging', label: 'Decharge' },
  { value: 'fault', label: 'Defaut' },
  { value: 'offline', label: 'Hors ligne' },
  { value: 'safe_state', label: 'Safe State' },
];

export function Batteries() {
  const { batteries, loading, error, refresh } = useBatteries(10000);
  const [search, setSearch] = useState('');
  const [filterSite, setFilterSite] = useState('all');
  const [filterState, setFilterState] = useState<BatteryState | 'all'>('all');

  const sites = useMemo(() => ['all', ...Array.from(new Set(batteries.map((b) => b.site_id))).sort()], [batteries]);

  const filtered = useMemo(() => {
    return batteries.filter((b) => {
      const matchSearch = !search ||
        b.asset_id.toLowerCase().includes(search.toLowerCase()) ||
        b.site_id.toLowerCase().includes(search.toLowerCase()) ||
        (b.manufacturer ?? '').toLowerCase().includes(search.toLowerCase());
      const matchSite = filterSite === 'all' || b.site_id === filterSite;
      const matchState = filterState === 'all' || b.state === filterState;
      return matchSearch && matchSite && matchState;
    });
  }, [batteries, search, filterSite, filterState]);

  const faultCount = batteries.filter((b) => b.state === 'fault').length;
  const batteriesWithSoc = batteries.filter((b) => b.soc_percent != null);
  const avgSoc =
    batteriesWithSoc.length > 0
      ? batteriesWithSoc.reduce((s, b) => s + (b.soc_percent ?? 0), 0) / batteriesWithSoc.length
      : 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Parc de Batteries</h1>
          <p className="text-sm text-slate-400 mt-0.5">Supervision et controle individuel</p>
        </div>
        <button onClick={refresh} className="p-2 rounded-lg bg-surface border border-border text-slate-400 hover:text-white hover:border-slate-500 transition-colors" aria-label="Actualiser">
          <RefreshCw className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-surface rounded-xl border border-border p-4 text-center">
          <p className="text-2xl font-bold text-white">{batteries.length}</p>
          <p className="text-xs text-slate-400 mt-0.5">Total batteries</p>
        </div>
        <div className={`bg-surface rounded-xl border p-4 text-center ${faultCount > 0 ? 'border-danger/50' : 'border-border'}`}>
          <p className={`text-2xl font-bold ${faultCount > 0 ? 'text-danger' : 'text-success'}`}>{faultCount}</p>
          <div className="flex items-center justify-center gap-1 mt-0.5">
            {faultCount > 0 && <AlertTriangle className="w-3 h-3 text-danger" aria-hidden="true" />}
            <p className="text-xs text-slate-400">Defauts</p>
          </div>
        </div>
        <div className="bg-surface rounded-xl border border-border p-4 text-center">
          <p className="text-2xl font-bold text-white">{avgSoc.toFixed(1)}%</p>
          <p className="text-xs text-slate-400 mt-0.5">SoC moyen</p>
        </div>
      </div>
      {error && <div role="alert" className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">Erreur: {error}</div>}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" aria-hidden="true" />
          <input type="search" placeholder="Rechercher par asset ID, site, fabricant..." value={search} onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 rounded-lg bg-surface border border-border text-white text-sm placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary" />
        </div>
        <select value={filterSite} onChange={(e) => setFilterSite(e.target.value)}
          className="px-3 py-2 rounded-lg bg-surface border border-border text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary">
          {sites.map((s) => <option key={s} value={s}>{s === 'all' ? 'Tous les sites' : s}</option>)}
        </select>
        <select value={filterState} onChange={(e) => setFilterState(e.target.value as BatteryState | 'all')}
          className="px-3 py-2 rounded-lg bg-surface border border-border text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary">
          {STATES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
      </div>
      <p className="text-xs text-slate-500">{filtered.length} batterie{filtered.length !== 1 ? 's' : ''} affichee{filtered.length !== 1 ? 's' : ''}{filtered.length !== batteries.length ? ` (sur ${batteries.length})` : ''}</p>
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="bg-surface rounded-xl border border-border p-4 space-y-3">
              <div className="skeleton h-4 w-24 rounded" />
              <div className="skeleton h-2 w-full rounded-full" />
              <div className="skeleton h-4 w-32 rounded" />
              <div className="skeleton h-8 w-full rounded-lg" />
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-16 text-center text-slate-500 text-sm">Aucune batterie ne correspond aux filtres</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4" role="list">
          {filtered.map((battery) => (
            <div key={battery.battery_id} role="listitem"><BatteryCard battery={battery} onCommandSent={refresh} /></div>
          ))}
        </div>
      )}
    </div>
  );
}
