import { useDraggable } from '@dnd-kit/core';
import { CSS } from '@dnd-kit/utilities';
import { Battery as BatteryIcon, GripVertical } from 'lucide-react';
import type { AggregateBatteryRef } from '../../api/types';

interface Props {
  battery: AggregateBatteryRef;
  /** Visual hint that this item is being dragged. */
  isDragOverlay?: boolean;
}

export function DraggableBattery({ battery, isDragOverlay = false }: Props) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: battery.battery_id,
    data: { battery },
  });

  const style = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging && !isDragOverlay ? 0.4 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      className={`
        group flex items-center gap-2 px-3 py-2 rounded-lg border bg-background
        cursor-grab active:cursor-grabbing select-none transition-all
        ${
          isDragOverlay
            ? 'border-primary shadow-lg shadow-primary/30'
            : 'border-border hover:border-slate-500'
        }
      `}
    >
      <GripVertical className="w-3.5 h-3.5 text-slate-500 group-hover:text-slate-300" aria-hidden="true" />
      <BatteryIcon className="w-3.5 h-3.5 text-primary flex-shrink-0" aria-hidden="true" />
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium text-white truncate">{battery.name}</p>
        <p className="text-[10px] text-slate-500 truncate">
          {battery.asset_id} · {Number(battery.capacity_kwh).toFixed(0)} kWh
        </p>
      </div>
    </div>
  );
}
