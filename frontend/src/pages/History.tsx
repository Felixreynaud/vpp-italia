import { useState, useEffect, useCallback, useMemo } from 'react';
import { Download, RefreshCw } from 'lucide-react';
import { AreaChart, Area, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { fetchHistory, fetchDispatchSessions } from '../api/client';
import type { HistoryPoint, DispatchSession } from '../api/types';

type TimeRange = '7j' | '30j' | '12m';
const TIME_RANGE_LABELS: Record<TimeRange, string> = { '7j': '7 jours', '30j': '30 jours', '12m': '12 mois' };

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; name: string; color: string }>;
  label?: string;
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-surface border border-border rounded-lg px-3 py-2 text-xs shadow-xl">
      <p className="text-slate-400 mb-1">{label}</p>
      {payload.map((p) => <p key={p.name} style={{ color: p.color }}>{`${p.name}: ${p.value.toFixed(1)}`}</p>)}
    </div>
  );
}

export function History() {
  const [timeRange, setTimeRange] = useState<TimeRange>('7j');
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [sessions, setSessions] = useState<DispatchSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [h, s] = await Promise.all([fetchHistory(), fetchDispatchSessions()]);
      setHistory(h); setSessions(s);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur de chargement');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load, timeRange]);

  const chartData = useMemo(() => {
    const limitH = timeRange === '7j' ? 168 : timeRange === '30j' ? 720 : 8760;
    const step = timeRange === '12m' ? 24 : 1;
    return history.slice(-limitH).filter((_, i) => i % step === 0).map((p) => ({
      ...p,
      label: new Date(p.timestamp).toLocaleString('fr-FR', {
        timeZone: 'Europe/Rome',
        month: 'short', day: 'numeric',
        hour: timeRange === '12m' ? undefined : '2-digit',
        minute: timeRange === '12m' ? undefined : '2-digit',
      }),
    }));
  }, [history, timeRange]);

  const handleExportCSV = () => {
    const header = 'timestamp,power_charge_kw,power_discharge_kw,soc_moyen,pnl_cumul_eur\n';
    const rows = history.map((p) => `${p.timestamp},${p.power_charge_kw},${p.power_discharge_kw},${p.soc_moyen.toFixed(2)},${p.pnl_cumul_eur.toFixed(2)}`).join('\n');
    const blob = new Blob([header + rows], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url;
    a.download = `vpp-history-${new Date().toISOString().slice(0, 10)}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  const handleExportJSON = () => {
    const blob = new Blob([JSON.stringify({ history, sessions }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url;
    a.download = `vpp-history-${new Date().toISOString().slice(0, 10)}.json`; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">Historique</h1>
          <p className="text-sm text-slate-400 mt-0.5">Performances et sessions de dispatch</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex rounded-lg border border-border overflow-hidden" role="group">
            {(Object.keys(TIME_RANGE_LABELS) as TimeRange[]).map((r) => (
              <button key={r} onClick={() => setTimeRange(r)} aria-pressed={timeRange === r}
                className={`px-4 py-2 text-sm font-medium transition-colors ${timeRange === r ? 'bg-primary text-white' : 'bg-surface text-slate-400 hover:text-white hover:bg-slate-700'}`}>
                {TIME_RANGE_LABELS[r]}
              </button>
            ))}
          </div>
          <button onClick={handleExportCSV} className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-surface border border-border text-slate-400 text-sm hover:text-white hover:border-slate-500 transition-colors">
            <Download className="w-4 h-4" aria-hidden="true" />CSV
          </button>
          <button onClick={handleExportJSON} className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-surface border border-border text-slate-400 text-sm hover:text-white hover:border-slate-500 transition-colors">
            <Download className="w-4 h-4" aria-hidden="true" />JSON
          </button>
          <button onClick={load} className="p-2 rounded-lg bg-surface border border-border text-slate-400 hover:text-white hover:border-slate-500 transition-colors" aria-label="Actualiser">
            <RefreshCw className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>
      </div>
      {error && <div role="alert" className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">Erreur: {error}</div>}
      {loading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => <div key={i} className="bg-surface rounded-xl border border-border p-5"><div className="skeleton h-4 w-48 rounded mb-4" /><div className="skeleton h-36 w-full rounded" /></div>)}
        </div>
      ) : (
        <div className="space-y-5">
          <div className="bg-surface rounded-xl border border-border p-5">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">Puissance agregee (kW)</h2>
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={chartData} margin={{ top: 5, right: 8, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="gradCharge" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="gradDischarge" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#22c55e" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="label" tick={{ fill: '#94a3b8', fontSize: 9 }} interval="preserveStartEnd" stroke="#334155" />
                <YAxis tick={{ fill: '#94a3b8', fontSize: 9 }} stroke="#334155" />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ fontSize: '11px', color: '#94a3b8' }} />
                <Area type="monotone" dataKey="power_charge_kw" name="Charge kW" stroke="#3b82f6" fill="url(#gradCharge)" strokeWidth={1.5} dot={false} />
                <Area type="monotone" dataKey="power_discharge_kw" name="Decharge kW" stroke="#22c55e" fill="url(#gradDischarge)" strokeWidth={1.5} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="bg-surface rounded-xl border border-border p-5">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">SoC Moyen du Parc (%)</h2>
            <ResponsiveContainer width="100%" height={150}>
              <LineChart data={chartData} margin={{ top: 5, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="label" tick={{ fill: '#94a3b8', fontSize: 9 }} interval="preserveStartEnd" stroke="#334155" />
                <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 9 }} stroke="#334155" />
                <Tooltip content={<CustomTooltip />} />
                <Line type="monotone" dataKey="soc_moyen" name="SoC %" stroke="#f59e0b" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="bg-surface rounded-xl border border-border p-5">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">P&L Cumulatif (€)</h2>
            <ResponsiveContainer width="100%" height={150}>
              <LineChart data={chartData} margin={{ top: 5, right: 8, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="label" tick={{ fill: '#94a3b8', fontSize: 9 }} interval="preserveStartEnd" stroke="#334155" />
                <YAxis tick={{ fill: '#94a3b8', fontSize: 9 }} stroke="#334155" tickFormatter={(v: number) => `${v.toFixed(0)}€`} />
                <Tooltip content={<CustomTooltip />} />
                <Line type="monotone" dataKey="pnl_cumul_eur" name="P&L €" stroke="#22c55e" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
      <div className="bg-surface rounded-xl border border-border overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="text-sm font-semibold text-slate-300">Sessions de Dispatch</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {['Date', 'Duree', 'Energie (MWh)', 'Revenu (€)', 'Marche'].map((h) => (
                  <th key={h} scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wide">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sessions.length === 0 && (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500 text-sm">{loading ? 'Chargement...' : 'Aucune session'}</td></tr>
              )}
              {sessions.map((s, i) => (
                <tr key={s.id} className={`border-b border-border/50 hover:bg-slate-700/30 transition-colors ${i % 2 === 0 ? '' : 'bg-slate-800/30'}`}>
                  <td className="px-4 py-3 text-slate-300 whitespace-nowrap">
                    {new Date(s.date).toLocaleDateString('fr-FR', { timeZone: 'Europe/Rome', day: 'numeric', month: 'short', year: '2-digit' })}
                  </td>
                  <td className="px-4 py-3 text-slate-300">
                    {s.duration_min >= 60 ? `${Math.floor(s.duration_min / 60)}h${String(s.duration_min % 60).padStart(2, '0')}` : `${s.duration_min}min`}
                  </td>
                  <td className="px-4 py-3 text-white font-medium">{s.energie_mwh.toFixed(2)}</td>
                  <td className="px-4 py-3"><span className="text-success font-medium">+{s.revenu_eur.toFixed(0)} €</span></td>
                  <td className="px-4 py-3"><span className="px-2 py-0.5 rounded-full text-xs font-medium bg-primary/20 text-primary">{s.marche}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
