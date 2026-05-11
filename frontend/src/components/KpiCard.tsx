import type { LucideIcon } from 'lucide-react';

interface KpiCardProps {
  label: string;
  value: string | number;
  unit?: string;
  icon: LucideIcon;
  trend?: 'up' | 'down' | 'neutral';
  trendValue?: string;
  color?: 'primary' | 'success' | 'warning' | 'danger';
  loading?: boolean;
}

const COLOR_MAP = {
  primary: 'text-primary bg-primary/10',
  success: 'text-success bg-success/10',
  warning: 'text-warning bg-warning/10',
  danger: 'text-danger bg-danger/10',
};

export function KpiCard({
  label,
  value,
  unit,
  icon: Icon,
  trend,
  trendValue,
  color = 'primary',
  loading = false,
}: KpiCardProps) {
  if (loading) {
    return (
      <div className="bg-surface rounded-xl border border-border p-4 space-y-3">
        <div className="skeleton h-4 w-24 rounded" />
        <div className="skeleton h-8 w-32 rounded" />
        <div className="skeleton h-3 w-16 rounded" />
      </div>
    );
  }

  return (
    <div className="bg-surface rounded-xl border border-border p-4 space-y-3 hover:border-slate-600 transition-colors">
      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-400 font-medium">{label}</span>
        <div className={`p-2 rounded-lg ${COLOR_MAP[color]}`} aria-hidden="true">
          <Icon className="w-4 h-4" />
        </div>
      </div>
      <div className="flex items-end gap-1">
        <span className="text-2xl font-bold text-white tabular-nums">
          {typeof value === 'number' ? value.toLocaleString('fr-FR') : value}
        </span>
        {unit && <span className="text-sm text-slate-400 mb-0.5">{unit}</span>}
      </div>
      {trendValue && (
        <div
          className={`flex items-center gap-1 text-xs font-medium
            ${trend === 'up' ? 'text-success' : trend === 'down' ? 'text-danger' : 'text-slate-400'}
          `}
          aria-label={`Tendance: ${trendValue}`}
        >
          {trend === 'up' && '↑'}
          {trend === 'down' && '↓'}
          {trend === 'neutral' && '→'}
          {trendValue}
        </div>
      )}
    </div>
  );
}
