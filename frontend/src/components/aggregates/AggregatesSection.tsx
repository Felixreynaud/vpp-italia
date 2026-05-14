import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { AlertCircle, CheckCircle2, Loader2, Plus, RefreshCw, Trash2 } from 'lucide-react';
import {
  assignBatteryToAggregate,
  createAggregate,
  deleteAggregate,
  listAggregates,
  listConfiguredBatteries,
  updateAggregate,
} from '../../api/client';
import type {
  Aggregate,
  AggregateBatteryRef,
  ConfiguredBattery,
} from '../../api/types';
import { AggregateCard } from './AggregateCard';
import { AggregateFormModal, type AggregateFormSubmitPayload } from './AggregateFormModal';
import { DraggableBattery } from './DraggableBattery';
import { UnassignedBatteriesZone } from './UnassignedBatteriesZone';

function batteryToRef(b: ConfiguredBattery): AggregateBatteryRef {
  return {
    battery_id: b.battery_id,
    asset_id: b.asset_id,
    name: b.name,
    capacity_kwh: Number(b.capacity_kwh),
    max_power_kw: Number(b.max_power_kw),
  };
}

function extractError(e: unknown, fallback: string): string {
  const err = e as { response?: { data?: { detail?: string } } };
  return err.response?.data?.detail ?? fallback;
}

export function AggregatesSection() {
  const [aggregates, setAggregates] = useState<Aggregate[]>([]);
  const [managedBatteries, setManagedBatteries] = useState<ConfiguredBattery[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Aggregate | null>(null);
  const [deletingTarget, setDeletingTarget] = useState<Aggregate | null>(null);
  const [draggedBattery, setDraggedBattery] = useState<AggregateBatteryRef | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor),
  );

  const load = useCallback(async () => {
    try {
      setError(null);
      const [aggs, bats] = await Promise.all([
        listAggregates(),
        listConfiguredBatteries({ active: true }),
      ]);
      setAggregates(aggs);
      setManagedBatteries(bats);
    } catch (e) {
      setError(extractError(e, 'Impossible de charger les agregats.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), 3500);
    return () => clearTimeout(t);
  }, [flash]);

  // List of batteries under management but not in any aggregate
  const unassignedBatteries = useMemo<AggregateBatteryRef[]>(() => {
    const assignedIds = new Set<string>();
    for (const a of aggregates) {
      for (const b of a.batteries) assignedIds.add(b.battery_id);
    }
    return managedBatteries
      .filter((b) => !assignedIds.has(b.battery_id))
      .map(batteryToRef);
  }, [aggregates, managedBatteries]);

  // ---- DnD handlers ----

  const handleDragStart = (event: DragStartEvent) => {
    const data = event.active.data.current as { battery?: AggregateBatteryRef } | undefined;
    if (data?.battery) setDraggedBattery(data.battery);
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    setDraggedBattery(null);
    const { active, over } = event;
    if (!over) return;

    const batteryId = String(active.id);
    const targetAggregateId =
      (over.data.current as { aggregateId?: string | null } | undefined)?.aggregateId ?? null;

    // Find current location to avoid useless API call
    let currentAggregateId: string | null = null;
    for (const agg of aggregates) {
      if (agg.batteries.some((b) => b.battery_id === batteryId)) {
        currentAggregateId = agg.aggregate_id;
        break;
      }
    }
    if (currentAggregateId === targetAggregateId) return;

    // Optimistic update — find battery info from current state
    let movedBattery: AggregateBatteryRef | null = null;
    for (const a of aggregates) {
      const found = a.batteries.find((b) => b.battery_id === batteryId);
      if (found) {
        movedBattery = found;
        break;
      }
    }
    if (!movedBattery) {
      const um = unassignedBatteries.find((b) => b.battery_id === batteryId);
      if (um) movedBattery = um;
    }
    if (!movedBattery) return;

    const previousState = aggregates;
    setAggregates((current) =>
      current.map((a) => ({
        ...a,
        batteries:
          a.aggregate_id === currentAggregateId
            ? a.batteries.filter((b) => b.battery_id !== batteryId)
            : a.aggregate_id === targetAggregateId
              ? [...a.batteries, movedBattery as AggregateBatteryRef]
              : a.batteries,
      })),
    );

    try {
      await assignBatteryToAggregate(batteryId, targetAggregateId);
      setFlash({
        kind: 'success',
        text: targetAggregateId
          ? `${movedBattery.name} ajoutee a l'agregat.`
          : `${movedBattery.name} retiree de son agregat.`,
      });
    } catch (e) {
      setAggregates(previousState);
      setFlash({ kind: 'error', text: extractError(e, 'Operation impossible.') });
    }
  };

  // ---- CRUD handlers ----

  const handleCreate = () => {
    setEditing(null);
    setFormOpen(true);
  };

  const handleEdit = (aggregate: Aggregate) => {
    setEditing(aggregate);
    setFormOpen(true);
  };

  const handleSubmitForm = async (payload: AggregateFormSubmitPayload) => {
    if (editing) {
      await updateAggregate(editing.aggregate_id, payload);
      setFlash({ kind: 'success', text: 'Agregat mis a jour.' });
    } else {
      await createAggregate(payload);
      setFlash({ kind: 'success', text: 'Agregat cree.' });
    }
    setFormOpen(false);
    setEditing(null);
    await load();
  };

  const handleDelete = (aggregate: Aggregate) => setDeletingTarget(aggregate);

  const confirmDelete = async () => {
    if (!deletingTarget) return;
    try {
      await deleteAggregate(deletingTarget.aggregate_id);
      setFlash({ kind: 'success', text: 'Agregat supprime.' });
      setDeletingTarget(null);
      await load();
    } catch (e) {
      setFlash({ kind: 'error', text: extractError(e, 'Suppression impossible.') });
      setDeletingTarget(null);
    }
  };

  // ---- Render ----

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-slate-400 text-sm py-6">
        <Loader2 className="w-4 h-4 animate-spin" /> Chargement des agregats...
      </div>
    );
  }

  return (
    <section className="space-y-4" aria-label="Agregats de batteries">
      <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-white">Agregats de batteries</h2>
          <p className="text-sm text-slate-400 mt-0.5">
            Glissez les batteries dans un agregat pour leur appliquer une strategie commune.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => { void load(); }}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface border border-border text-slate-300 hover:bg-slate-700/50 transition-colors"
          >
            <RefreshCw className="w-4 h-4" /> Rafraichir
          </button>
          <button
            onClick={handleCreate}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-primary text-white hover:bg-primary/90 transition-colors"
          >
            <Plus className="w-4 h-4" /> Creer un agregat
          </button>
        </div>
      </header>

      {error && (
        <div role="alert" className="flex items-center gap-2 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
          <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
        </div>
      )}

      {flash && (
        <div
          role="status"
          className={`flex items-center gap-2 p-3 rounded-lg border text-sm ${
            flash.kind === 'success'
              ? 'bg-success/10 border-success/30 text-success'
              : 'bg-danger/10 border-danger/30 text-danger'
          }`}
        >
          {flash.kind === 'success' ? (
            <CheckCircle2 className="w-4 h-4" />
          ) : (
            <AlertCircle className="w-4 h-4" />
          )}
          {flash.text}
        </div>
      )}

      <DndContext sensors={sensors} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
        {/* Aggregates grid */}
        {aggregates.length === 0 ? (
          <div className="p-8 text-center text-sm text-slate-500 bg-surface rounded-2xl border border-dashed border-border">
            Aucun agregat. Cliquez sur <strong className="text-white">Creer un agregat</strong> pour commencer.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {aggregates.map((a) => (
              <AggregateCard
                key={a.aggregate_id}
                aggregate={a}
                onEdit={handleEdit}
                onDelete={handleDelete}
              />
            ))}
          </div>
        )}

        {/* Unassigned batteries */}
        <UnassignedBatteriesZone batteries={unassignedBatteries} />

        {/* Drag overlay — visual feedback under the cursor */}
        <DragOverlay>
          {draggedBattery ? <DraggableBattery battery={draggedBattery} isDragOverlay /> : null}
        </DragOverlay>
      </DndContext>

      {formOpen && (
        <AggregateFormModal
          existing={editing}
          onClose={() => {
            setFormOpen(false);
            setEditing(null);
          }}
          onSubmit={handleSubmitForm}
        />
      )}

      {deletingTarget && (
        <div
          className="fixed inset-0 z-40 bg-black/60 flex items-center justify-center p-4"
          onClick={() => setDeletingTarget(null)}
        >
          <div
            className="bg-surface rounded-2xl border border-border max-w-md w-full p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 mb-4">
              <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-danger/20">
                <Trash2 className="w-5 h-5 text-danger" />
              </div>
              <h2 className="text-lg font-semibold text-white">Supprimer l'agregat ?</h2>
            </div>
            <p className="text-sm text-slate-400 mb-6">
              L'agregat <strong className="text-white">{deletingTarget.name}</strong> sera
              supprime. Les {deletingTarget.batteries.length} batteries qu'il contient seront
              <strong className="text-white"> liberees</strong> (deplacees dans "Sans agregat"),
              <strong className="text-white"> pas supprimees</strong>.
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setDeletingTarget(null)}
                className="px-4 py-2 rounded-lg bg-slate-700 text-white hover:bg-slate-600 transition-colors"
              >
                Annuler
              </button>
              <button
                onClick={() => { void confirmDelete(); }}
                className="px-4 py-2 rounded-lg bg-danger text-white hover:bg-danger/90 transition-colors"
              >
                Supprimer
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
