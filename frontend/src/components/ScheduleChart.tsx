import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer, Cell, Legend } from 'recharts';
import type { ScheduleSlot } from '../api/types';

interface ScheduleChartProps {
  schedule: ScheduleSlot[];
  compareSchedule?: ScheduleSlot[];
  showCompare?: boolean;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; name: string; color: string }>;
  label?: string | number;
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-surface border border-border rounded-lg px-3 py-2 text-sm shadow-xl">
      <p className="text-slate-400 mb-1">{`H${String(label).padStart(2, '0')}:00`}</p>
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }} className="font-bold">
          {`${p.name}: ${p.value > 0 ? '+' : ''}${p.value.toFixed(0)} kW`}
        </p>
      ))}
    </div>
  );
}

export function ScheduleChart({ schedule, compareSchedule, showCompare = false }: ScheduleChartProps) {
  const data = schedule.map((slot) => {
    const comp = compareSchedule?.find((s) => s.hour === slot.hour);
    return { hour: slot.hour, planning_ia: slot.power_kw, planning_actuel: comp?.power_kw ?? 0 };
  });

  if (schedule.length === 0) {
    return <div className="h-52 flex items-center justify-center text-slate-500 text-sm">Lancez une optimisation pour voir le planning</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -10, bottom: 0 }} barCategoryGap="20%">
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
        <XAxis dataKey="hour" tick={{ fill: '#94a3b8', fontSize: 10 }} tickFormatter={(v: number) => `${v}h`} interval={3} stroke="#334155" />
        <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} stroke="#334155" tickFormatter={(v: number) => `${v}`} label={{ value: 'kW', fill: '#94a3b8', fontSize: 10, position: 'insideTop', offset: -2 }} />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine y={0} stroke="#475569" strokeWidth={1.5} />
        {showCompare && <Legend wrapperStyle={{ fontSize: '11px', color: '#94a3b8' }} />}
        <Bar dataKey="planning_ia" name="Planning IA" radius={[2, 2, 0, 0]}>
          {data.map((entry, index) => (
            <Cell key={`ia-${index}`} fill={entry.planning_ia >= 0 ? '#22c55e' : '#3b82f6'} fillOpacity={0.85} />
          ))}
        </Bar>
        {showCompare && <Bar dataKey="planning_actuel" name="Planning actuel" radius={[2, 2, 0, 0]} fill="#f59e0b" fillOpacity={0.5} />}
      </BarChart>
    </ResponsiveContainer>
  );
}
