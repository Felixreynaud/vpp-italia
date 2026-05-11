import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer } from 'recharts';
import type { MGPPrice } from '../api/types';

interface PriceChartProps { prices: MGPPrice[]; }

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; name: string }>;
  label?: string | number;
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-surface border border-border rounded-lg px-3 py-2 text-sm shadow-xl">
      <p className="text-slate-400 mb-1">{`H${String(label).padStart(2, '0')}:00`}</p>
      <p className="font-bold text-warning">{`${payload[0].value.toFixed(2)} €/MWh`}</p>
    </div>
  );
}

export function PriceChart({ prices }: PriceChartProps) {
  const currentHour = new Date().toLocaleString('en-US', { timeZone: 'Europe/Rome', hour: 'numeric', hour12: false });
  const currentH = parseInt(currentHour, 10);

  if (prices.length === 0) {
    return <div className="h-40 flex items-center justify-center text-slate-500 text-sm">Chargement des prix...</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={160}>
      <LineChart data={prices} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
        <XAxis dataKey="hour" tick={{ fill: '#94a3b8', fontSize: 10 }} tickFormatter={(v: number) => `${v}h`} interval={3} stroke="#334155" />
        <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} stroke="#334155" />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine x={currentH} stroke="#f59e0b" strokeWidth={2} strokeDasharray="4 2" label={{ value: 'Now', fill: '#f59e0b', fontSize: 9, position: 'top' }} />
        <Line type="monotone" dataKey="price_eur_mwh" stroke="#3b82f6" strokeWidth={2} dot={false} activeDot={{ r: 4, fill: '#3b82f6', stroke: '#0f172a', strokeWidth: 2 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}
