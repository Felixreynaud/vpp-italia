import { useDroppable } from '@dnd-kit/core';
import { Inbox } from 'lucide-react';
import type { AggregateBatteryRef } from '../../api/types';
import { DraggableBattery } from './DraggableBattery';

interface Props {
  batteries: AggregateBatteryRef[];
}

/** Drop zone for unassigned batteries (battery.aggregate_id === null). */
export function UnassignedBatteriesZone({ batteries }: Props) {
  const { setNodeRef, isOver } = useDroppable({
    id: 'unassigned',
    data: { aggregateId: null },
  });

  return (
    <div
      ref={setNodeRef}
      className={`
        bg-surface rounded-2xl border p-4 transition-all
        ${isOver ? 'border-warning shadow-lg shadow-warning/20 bg-warning/5' : 'border-border'}
      `}
    >
      <div className="flex items-center gap-2 mb-3">
        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-slate-700">
          <Inbox className="w-4 h-4 text-slate-300" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-white">Batteries sans agregat</h3>
          <p className="text-[11px] text-slate-400 mt-0.5">
            {batteries.length} batterie{batteries.length > 1 ? 's' : ''} sous management,
            pilotable{batteries.length > 1 ? 's' : ''} individuellement
          </p>
        </div>
      </div>

      <div
        className={`
          grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5 min-h-[80px] rounded-lg p-2
          ${isOver ? 'bg-warning/10 border-2 border-dashed border-warning' : ''}
        `}
      >
        {batteries.length === 0 ? (
          <div className="col-span-full flex items-center justify-center h-16 text-xs text-slate-500 border-2 border-dashed border-border rounded-lg">
            Toutes les batteries sont assignees a un agregat
          </div>
        ) : (
          batteries.map((battery) => (
            <DraggableBattery key={battery.battery_id} battery={battery} />
          ))
        )}
      </div>
    </div>
  );
}
