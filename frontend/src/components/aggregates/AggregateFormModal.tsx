import { useEffect, useState, type FormEvent } from 'react';
import { AlertCircle, Layers, Loader2 } from 'lucide-react';
import type {
  Aggregate,
  AggregateStrategy,
  MGPZone,
  MarketName,
} from '../../api/types';

const STRATEGIES: { value: AggregateStrategy; label: string }[] = [
  { value: 'arbitrage_mgp', label: 'Arbitrage MGP' },
  { value: 'autoconsommation', label: 'Autoconsommation (a venir)' },
  { value: 'msd', label: 'MSD (a venir)' },
  { value: 'stochastique', label: 'Stochastique (a venir)' },
];

const MARKETS: { value: MarketName; label: string }[] = [
  { value: 'MGP', label: 'MGP' },
  { value: 'MI', label: 'MI' },
  { value: 'MSD', label: 'MSD' },
  { value: 'MSD_GME', label: 'MSD_GME' },
  { value: 'MB', label: 'MB' },
];

const ZONES: { value: MGPZone; label: string }[] = [
  { value: 'NORD', label: 'Nord' },
  { value: 'CNOR', label: 'Centre-Nord' },
  { value: 'CSUD', label: 'Centre-Sud' },
  { value: 'SUD', label: 'Sud' },
  { value: 'CALA', label: 'Calabre' },
  { value: 'SARD', label: 'Sardaigne' },
  { value: 'SICI', label: 'Sicile' },
  { value: 'PUN', label: 'PUN' },
];

export interface AggregateFormSubmitPayload {
  name: string;
  description: string | null;
  strategy_type: AggregateStrategy;
  target_market: MarketName | null;
  target_zone: MGPZone | null;
}

interface Props {
  existing?: Aggregate | null;
  onClose: () => void;
  onSubmit: (payload: AggregateFormSubmitPayload) => Promise<void>;
}

export function AggregateFormModal({ existing, onClose, onSubmit }: Props) {
  const isEdit = Boolean(existing);
  const [name, setName] = useState(existing?.name ?? '');
  const [description, setDescription] = useState(existing?.description ?? '');
  const [strategy, setStrategy] = useState<AggregateStrategy>(
    existing?.strategy_type ?? 'arbitrage_mgp'
  );
  const [market, setMarket] = useState<MarketName | ''>(existing?.target_market ?? '');
  const [zone, setZone] = useState<MGPZone | ''>(existing?.target_zone ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (existing) {
      setName(existing.name);
      setDescription(existing.description ?? '');
      setStrategy(existing.strategy_type);
      setMarket(existing.target_market ?? '');
      setZone(existing.target_zone ?? '');
    }
  }, [existing]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError('Le nom est obligatoire.');
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim(),
        description: description.trim() || null,
        strategy_type: strategy,
        target_market: market || null,
        target_zone: zone || null,
      });
    } catch (e: unknown) {
      const apiError = e as { response?: { status?: number; data?: { detail?: string } } };
      const detail = apiError.response?.data?.detail;
      if (apiError.response?.status === 409) {
        setError(detail ?? 'Un agregat avec ce nom existe deja.');
      } else if (detail) {
        setError(detail);
      } else {
        setError('Operation impossible.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-40 bg-black/60 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-surface rounded-2xl border border-border max-w-md w-full p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 mb-4">
          <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-primary/20">
            <Layers className="w-5 h-5 text-primary" />
          </div>
          <h2 className="text-lg font-semibold text-white">
            {isEdit ? 'Modifier l\'agregat' : 'Creer un agregat'}
          </h2>
        </div>

        <form onSubmit={(e) => { void handleSubmit(e); }} className="space-y-4">
          {error && (
            <div role="alert" className="flex items-center gap-2 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
              <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
            </div>
          )}

          <div className="space-y-1">
            <label htmlFor="agg_name" className="block text-sm font-medium text-slate-300">
              Nom *
            </label>
            <input
              id="agg_name" type="text" required maxLength={128}
              value={name} onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white focus:outline-none focus:ring-2 focus:ring-primary"
              placeholder="Lombardie MGP"
            />
          </div>

          <div className="space-y-1">
            <label htmlFor="agg_desc" className="block text-sm font-medium text-slate-300">
              Description
            </label>
            <textarea
              id="agg_desc" rows={2} maxLength={512}
              value={description} onChange={(e) => setDescription(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white focus:outline-none focus:ring-2 focus:ring-primary"
              placeholder="Optionnel"
            />
          </div>

          <div className="space-y-1">
            <label htmlFor="agg_strategy" className="block text-sm font-medium text-slate-300">
              Strategie d'optimisation
            </label>
            <select
              id="agg_strategy" value={strategy}
              onChange={(e) => setStrategy(e.target.value as AggregateStrategy)}
              className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white focus:outline-none focus:ring-2 focus:ring-primary"
            >
              {STRATEGIES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label htmlFor="agg_market" className="block text-sm font-medium text-slate-300">
                Marche cible
              </label>
              <select
                id="agg_market" value={market}
                onChange={(e) => setMarket(e.target.value as MarketName | '')}
                className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="">(aucun)</option>
                {MARKETS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label htmlFor="agg_zone" className="block text-sm font-medium text-slate-300">
                Zone
              </label>
              <select
                id="agg_zone" value={zone}
                onChange={(e) => setZone(e.target.value as MGPZone | '')}
                className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="">(aucune)</option>
                {ZONES.map((z) => <option key={z.value} value={z.value}>{z.label}</option>)}
              </select>
            </div>
          </div>

          <p className="text-xs text-slate-500">
            Marche et zone sont optionnels. Tu pourras les definir plus tard.
          </p>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button" onClick={onClose}
              className="px-4 py-2 rounded-lg bg-slate-700 text-white hover:bg-slate-600 transition-colors"
            >
              Annuler
            </button>
            <button
              type="submit" disabled={submitting}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              {submitting ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Enregistrement...</>
              ) : (
                isEdit ? 'Enregistrer' : 'Creer'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
