import { useState, useEffect, useCallback } from 'react';
import { fetchBatteries } from '../api/client';
import type { Battery } from '../api/types';

interface UseBatteriesReturn {
  batteries: Battery[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useBatteries(pollingIntervalMs = 10000): UseBatteriesReturn {
  const [batteries, setBatteries] = useState<Battery[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchBatteries();
      setBatteries(data);
      setError(null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Erreur de chargement';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const interval = setInterval(() => { void load(); }, pollingIntervalMs);
    return () => clearInterval(interval);
  }, [load, pollingIntervalMs]);

  return { batteries, loading, error, refresh: load };
}
