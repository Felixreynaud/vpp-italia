import { useState, useEffect, useCallback } from 'react';
import { fetchFleetMetrics, fetchMGPPrices } from '../api/client';
import type { FleetMetrics, MGPPrice } from '../api/types';

interface UseFleetReturn {
  metrics: FleetMetrics | null;
  mgpPrices: MGPPrice[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useFleet(pollingIntervalMs = 10000, zone = 'NORD'): UseFleetReturn {
  const [metrics, setMetrics] = useState<FleetMetrics | null>(null);
  const [mgpPrices, setMgpPrices] = useState<MGPPrice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, p] = await Promise.all([fetchFleetMetrics(), fetchMGPPrices(zone)]);
      setMetrics(m);
      setMgpPrices(p.prices);
      setError(null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Erreur de chargement';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [zone]);

  useEffect(() => {
    void load();
    const interval = setInterval(() => { void load(); }, pollingIntervalMs);
    return () => clearInterval(interval);
  }, [load, pollingIntervalMs]);

  return { metrics, mgpPrices, loading, error, refresh: load };
}
