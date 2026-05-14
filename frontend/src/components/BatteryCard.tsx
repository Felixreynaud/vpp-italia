import { useState } from 'react';
import { Thermometer, Zap, Loader2 } from 'lucide-react';
import { sendBatteryCommand } from '../api/client';
import type { Battery } from '../api/types';

interface BatteryCardProps {
  battery: Battery;
  onCommandSent?: () => void;
}

const STATE_BADGE: Record<Battery['state'], { label: string; className: string }> = {
  idle: { label: 'Inactif', className: 'bg-slate-700 text-slate-300' },
  charging: { label: 'Charge', className: 'bg-success/20 text-success' },
  discharging: { label: 'Decharge', className: 'bg-primary/20 text-primary' },
  fault: { label: 'Defaut', className: 'bg-danger/20 text-danger' },
  offline: { label: 'Hors ligne', className: 'bg-slate-700 text-slate-500' },
  safe_state: { label: 'Safe State', className: 'bg-warning/20 text-warning' },
};

function getSocBarColor(soc: number): string {
  if (soc < 10 || soc > 92) return 'bg-danger';
  if (soc < 20 || soc > 85) return 'bg-warning';
  return 'bg-success';
}

export function BatteryCard({ battery, onCommandSent }: BatteryCardProps) {
  const [loading, setLoading] = useState<'charge' | 'discharge' | 'stop' | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const badge = STATE_BADGE[battery.state] ?? STATE_BADGE.offline;

  const handleCommand = async (cmd: 'charge' | 'discharge' | 'stop') => {
    setLoading(cmd);
    try {
      await sendBatteryCommand(battery.battery_id, {
        command: cmd,
        power_kw: cmd !== 'stop' ? battery.max_power_kw * 0.8 : undefined,
      });
      setToast(`Commande ${cmd} envoyee`);
      onCommandSent?.();
    } catch {
      setToast('Erreur envoi commande');
    } finally {
      setLoading(null);
      setTimeout(() => setToast(null), 3000);
    }
  };

  const isFaultOrOffline = battery.state === 'fault' || battery.state === 'offline';

  return (
    <div
      className={`bg-surface rounded-xl border transition-colors p-4 space-y-3 relative
        ${battery.state === 'fault' ? 'border-danger/50' : battery.state === 'safe_state' ? 'border-warning/50' : 'border-border hover:border-slate-600'}`}
      aria-label={`Batterie ${battery.asset_id}`}
    >
      {toast && (
        <div className="absolute top-2 right-2 text-xs bg-slate-700 text-white px-2 py-1 rounded shadow z-10" role="status" aria-live="polite">
          {toast}
        </div>
      )}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-white font-semibold text-sm truncate">{battery.asset_id}</p>
          <p className="text-slate-500 text-xs truncate">{battery.site_id}</p>
        </div>
        <span className={`px-2 py-0.5 rounded-full text-xs font-medium flex-shrink-0 ${badge.className}`}>
          {badge.label}
        </span>
      </div>
      <div>
        <div className="flex justify-between text-xs mb-1">
          <span className="text-slate-400">SoC</span>
          <span className="font-bold text-white">
            {battery.soc_percent != null ? `${Number(battery.soc_percent).toFixed(1)}%` : '—'}
          </span>
        </div>
        <div
          className="w-full h-2 bg-slate-700 rounded-full overflow-hidden"
          role="progressbar"
          aria-valuenow={battery.soc_percent ?? 0}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={
            battery.soc_percent != null
              ? `SoC: ${Number(battery.soc_percent).toFixed(1)}%`
              : 'SoC: pas de donnees'
          }
        >
          <div
            className={`h-full rounded-full transition-all duration-500 ${getSocBarColor(battery.soc_percent ?? 0)}`}
            style={{ width: `${Math.min(100, battery.soc_percent ?? 0)}%` }}
          />
        </div>
      </div>
      <div className="flex items-center gap-3 text-xs">
        <div className="flex items-center gap-1 text-slate-400">
          <Zap className="w-3.5 h-3.5 text-primary" aria-hidden="true" />
          {battery.power_kw != null ? (
            <span
              className={
                battery.power_kw > 0
                  ? 'text-success'
                  : battery.power_kw < 0
                    ? 'text-primary'
                    : 'text-slate-400'
              }
            >
              {battery.power_kw > 0 ? '+' : ''}
              {Number(battery.power_kw).toFixed(0)} kW
            </span>
          ) : (
            <span className="text-slate-500">— kW</span>
          )}
        </div>
        <div className="flex items-center gap-1 text-slate-400">
          <Thermometer className="w-3.5 h-3.5 text-warning" aria-hidden="true" />
          {battery.temperature_c != null ? (
            <span
              className={
                battery.temperature_c > 50
                  ? 'text-danger'
                  : battery.temperature_c > 40
                    ? 'text-warning'
                    : 'text-slate-300'
              }
            >
              {Number(battery.temperature_c).toFixed(1)}&deg;C
            </span>
          ) : (
            <span className="text-slate-500">—&deg;C</span>
          )}
        </div>
        <div className="ml-auto text-slate-500 truncate text-right">
          {battery.manufacturer ?? battery.protocol}
        </div>
      </div>
      <div className="flex gap-1.5 pt-1">
        <button
          onClick={() => { void handleCommand('charge'); }}
          disabled={!!loading || isFaultOrOffline}
          className="flex-1 flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium bg-success/10 text-success hover:bg-success/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          aria-label={`Charger batterie ${battery.asset_id}`}
        >
          {loading === 'charge' ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
          Charger
        </button>
        <button
          onClick={() => { void handleCommand('discharge'); }}
          disabled={!!loading || isFaultOrOffline}
          className="flex-1 flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          aria-label={`Decharger batterie ${battery.asset_id}`}
        >
          {loading === 'discharge' ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
          Decharger
        </button>
        <button
          onClick={() => { void handleCommand('stop'); }}
          disabled={!!loading || battery.state === 'offline'}
          className="flex-1 flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium bg-slate-700 text-slate-300 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          aria-label={`Arreter batterie ${battery.asset_id}`}
        >
          {loading === 'stop' ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
          Stop
        </button>
      </div>
    </div>
  );
}
