import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Activity, AlertCircle, Loader2 } from 'lucide-react';
import { login } from '../api/client';

export function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const resp = await login({ username, password });
      localStorage.setItem('vpp_token', resp.access_token);
      void navigate('/');
    } catch {
      setError('Identifiants invalides. Veuillez reessayer.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-8">
          <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-primary/20 mb-4">
            <Activity className="w-7 h-7 text-primary" />
          </div>
          <h1 className="text-2xl font-bold text-white">VPP Italia</h1>
          <p className="text-sm text-slate-400 mt-1">Pannello Operatore</p>
        </div>
        <form
          onSubmit={(e) => { void handleSubmit(e); }}
          className="bg-surface rounded-2xl border border-border p-6 space-y-4"
        >
          <h2 className="text-lg font-semibold text-white text-center">Connexion</h2>
          {error && (
            <div role="alert" className="flex items-center gap-2 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              {error}
            </div>
          )}
          <div className="space-y-1">
            <label htmlFor="username" className="block text-sm font-medium text-slate-300">Nom d'utilisateur</label>
            <input
              id="username" type="text" autoComplete="username" required
              value={username} onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent transition-colors"
              placeholder="operatore"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="password" className="block text-sm font-medium text-slate-300">Mot de passe</label>
            <input
              id="password" type="password" autoComplete="current-password" required
              value={password} onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent transition-colors"
              placeholder="••••••••"
            />
          </div>
          <button
            type="submit" disabled={loading}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-white font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors mt-2"
            aria-busy={loading}
          >
            {loading ? <><Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />Connexion...</> : 'Se connecter'}
          </button>
          <p className="text-center pt-1">
            <Link to="/forgot-password" className="text-sm text-primary hover:underline">
              Mot de passe oublie ?
            </Link>
          </p>
        </form>
        <p className="text-center text-xs text-slate-500 mt-4">VPP Italia v0.1 — Environnement securise</p>
      </div>
    </div>
  );
}
