import { useDroppable } from '@dnd-kit/core';
import { Edit2, Trash2, Layers, AlertCircle } from 'lucide-react';
import type { Aggregate } from '../../api/types';
import { DraggableBattery } from './DraggableBattery';

const STRATEGY_LABELS: Record<string, string> = {
  arbitrage_mgp: 'Arbitrage MGP',
  autoconsommation: 'Autoconsommation',
  msd: 'MSD',
  stochastique: 'Stochastique',
};

interface Props {
  aggregate: Aggregate;
  onEdit: (aggregate: Aggregate) => void;
  onDelete: (aggregate: Aggregate) => void;
}

export function AggregateCard({ aggregate, onEdit, onDelete }: Props) {
  const { setNodeRef, isOver } = useDroppable({
    id: `aggregate:${aggregate.aggregate_id}`,
    data: { aggregateId: aggregate.aggregate_id },
  });

  const totalCapacity = aggregate.batteries.reduce(
    (sum, b) => sum + Number(b.capacity_kwh),
    0
  );
  const totalPower = aggregate.batteries.reduce(
    (sum, b) => sum + Number(b.max_power_kw),
    0
  );

  return (
    <div
      ref={setNodeRef}
      className={`
        bg-surface rounded-2xl border p-4 transition-all
        ${isOver ? 'border-primary shadow-lg shadow-primary/20 bg-primary/5' : 'border-border'}
      `}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-start gap-2 min-w-0">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary/20 flex-shrink-0">
            <Layers className="w-4 h-4 text-primary" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-white truncate">{aggregate.name}</h3>
            <p className="text-[11px] text-slate-400 mt-0.5">
              {STRATEGY_LABELS[aggregate.strategy_type] ?? aggregate.strategy_type}
              {aggregate.target_zone ? ` · ${aggregate.target_zone}` : ''}
              {aggregate.target_market ? ` · ${aggregate.target_market}` : ''}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            onClick={() => onEdit(aggregate)}
            className="p-1.5 rounded text-slate-400 hover:bg-slate-700 hover:text-white transition-colors"
            title="Modifier"
            aria-label={`Modifier ${aggregate.name}`}
          >
            <Edit2 className="w-3.5 h-3.5" aria-hidden="true" />
          </button>
          <button
            onClick={() => onDelete(aggregate)}
            className="p-1.5 rounded text-slate-400 hover:bg-danger/20 hover:text-danger transition-colors"
            title="Supprimer"
            aria-label={`Supprimer ${aggregate.name}`}
          >
            <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>

      {aggregate.description && (
        <p className="text-xs text-slate-500 mb-3 line-clamp-2">{aggregate.description}</p>
      )}

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2 mb-3 text-center">
        <div className="bg-background rounded-lg py-2">
          <p className="text-base font-bold text-white">{aggregate.batteries.length}</p>
          <p className="text-[10px] text-slate-500 uppercase tracking-wide">batteries</p>
        </div>
        <div className="bg-background rounded-lg py-2">
          <p className="text-base font-bold text-white">
            {totalCapacity >= 1000
              ? `${(totalCapacity / 1000).toFixed(1)}M`
              : totalCapacity.toFixed(0)}
          </p>
          <p className="text-[10px] text-slate-500 uppercase tracking-wide">
            {totalCapacity >= 1000 ? 'MWh' : 'kWh'}
          </p>
        </div>
        <div className="bg-background rounded-lg py-2">
          <p className="text-base font-bold text-white">{totalPower.toFixed(0)}</p>
          <p className="text-[10px] text-slate-500 uppercase tracking-wide">kW</p>
        </div>
      </div>

      {/* Battery list (drop zone) */}
      <div
        className={`
          space-y-1.5 min-h-[80px] rounded-lg p-2 transition-colors
          ${isOver ? 'bg-primary/10 border-2 border-dashed border-primary' : ''}
        `}
      >
        {aggregate.batteries.length === 0 ? (
          <div className="flex items-center justify-center h-16 text-xs text-slate-500 border-2 border-dashed border-border rounded-lg">
            <AlertCircle className="w-3.5 h-3.5 mr-1.5" aria-hidden="true" />
            Glissez des batteries ici
          </div>
        ) : (
          aggregate.batteries.map((battery) => (
            <DraggableBattery key={battery.battery_id} battery={battery} />
          ))
        )}
      </div>
    </div>
  );
}
