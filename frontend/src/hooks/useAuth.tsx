import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { fetchMe } from '../api/client';
import type { UserProfile } from '../api/types';

interface AuthState {
  user: UserProfile | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  clear: () => void;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const token = localStorage.getItem('vpp_token');
    if (!token) {
      setUser(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const profile = await fetchMe();
      setUser(profile);
    } catch {
      setError('Profil indisponible');
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const clear = useCallback(() => {
    setUser(null);
    setError(null);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Re-fetch the profile if the token changes in another tab (best effort).
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'vpp_token') void refresh();
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [refresh]);

  const value = useMemo<AuthState>(
    () => ({ user, loading, error, refresh, clear }),
    [user, loading, error, refresh, clear]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (ctx === undefined) {
    throw new Error('useAuth must be used inside <AuthProvider>');
  }
  return ctx;
}
