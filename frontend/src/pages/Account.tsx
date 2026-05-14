import { useEffect, useState, type FormEvent } from 'react';
import { AlertCircle, CheckCircle2, KeyRound, Loader2, User as UserIcon } from 'lucide-react';
import { changePassword, fetchMe } from '../api/client';
import type { UserProfile } from '../api/types';

export function Account() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingProfile, setLoadingProfile] = useState(true);

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await fetchMe();
        if (!cancelled) setProfile(me);
      } catch {
        if (!cancelled) setLoadError('Impossible de charger votre profil.');
      } finally {
        if (!cancelled) setLoadingProfile(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const validate = (): string | null => {
    if (!currentPassword) return 'Le mot de passe actuel est requis.';
    if (newPassword.length < 10) return 'Le nouveau mot de passe doit faire au moins 10 caracteres.';
    if (!/[A-Z]/.test(newPassword)) return 'Le nouveau mot de passe doit contenir au moins une majuscule.';
    if (!/[0-9]/.test(newPassword)) return 'Le nouveau mot de passe doit contenir au moins un chiffre.';
    if (newPassword === currentPassword) return 'Le nouveau mot de passe doit etre different de l\'actuel.';
    if (newPassword !== confirmPassword) return 'La confirmation ne correspond pas.';
    return null;
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setFormSuccess(null);
    const err = validate();
    if (err) {
      setFormError(err);
      return;
    }
    setSubmitting(true);
    try {
      await changePassword({ current_password: currentPassword, new_password: newPassword });
      setFormSuccess('Mot de passe mis a jour.');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (e: unknown) {
      const apiError = e as { response?: { status?: number; data?: { detail?: string } } };
      const status = apiError.response?.status;
      const detail = apiError.response?.data?.detail;
      if (status === 400 && detail) setFormError(detail);
      else if (status === 422) setFormError('Mot de passe trop faible.');
      else setFormError('Impossible de modifier le mot de passe.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-3xl space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold text-white">Mon compte</h1>
        <p className="text-sm text-slate-400">Profil utilisateur et gestion du mot de passe.</p>
      </header>

      <section
        aria-label="Profil"
        className="bg-surface rounded-2xl border border-border p-6"
      >
        <div className="flex items-center gap-3 mb-4">
          <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-primary/20">
            <UserIcon className="w-5 h-5 text-primary" />
          </div>
          <h2 className="text-lg font-semibold text-white">Profil</h2>
        </div>

        {loadingProfile && (
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <Loader2 className="w-4 h-4 animate-spin" /> Chargement...
          </div>
        )}

        {loadError && (
          <div role="alert" className="flex items-center gap-2 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
            <AlertCircle className="w-4 h-4" /> {loadError}
          </div>
        )}

        {profile && !loadError && (
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <div>
              <dt className="text-slate-400">Email</dt>
              <dd className="text-white font-medium">{profile.email}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Nom complet</dt>
              <dd className="text-white font-medium">{profile.full_name}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Role</dt>
              <dd className="text-white font-medium capitalize">{profile.role}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Statut</dt>
              <dd className="text-white font-medium">{profile.is_active ? 'Actif' : 'Inactif'}</dd>
            </div>
          </dl>
        )}
      </section>

      <section
        aria-label="Changer le mot de passe"
        className="bg-surface rounded-2xl border border-border p-6"
      >
        <div className="flex items-center gap-3 mb-4">
          <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-primary/20">
            <KeyRound className="w-5 h-5 text-primary" />
          </div>
          <h2 className="text-lg font-semibold text-white">Changer le mot de passe</h2>
        </div>

        <form onSubmit={(e) => { void handleSubmit(e); }} className="space-y-4 max-w-md">
          {formError && (
            <div role="alert" className="flex items-center gap-2 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
              <AlertCircle className="w-4 h-4 flex-shrink-0" /> {formError}
            </div>
          )}
          {formSuccess && (
            <div role="status" className="flex items-center gap-2 p-3 rounded-lg bg-success/10 border border-success/30 text-success text-sm">
              <CheckCircle2 className="w-4 h-4 flex-shrink-0" /> {formSuccess}
            </div>
          )}

          <div className="space-y-1">
            <label htmlFor="current_password" className="block text-sm font-medium text-slate-300">
              Mot de passe actuel
            </label>
            <input
              id="current_password"
              type="password"
              autoComplete="current-password"
              required
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent transition-colors"
            />
          </div>

          <div className="space-y-1">
            <label htmlFor="new_password" className="block text-sm font-medium text-slate-300">
              Nouveau mot de passe
            </label>
            <input
              id="new_password"
              type="password"
              autoComplete="new-password"
              required
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent transition-colors"
            />
            <p className="text-xs text-slate-500">10 caracteres minimum, dont au moins une majuscule et un chiffre.</p>
          </div>

          <div className="space-y-1">
            <label htmlFor="confirm_password" className="block text-sm font-medium text-slate-300">
              Confirmer le nouveau mot de passe
            </label>
            <input
              id="confirm_password"
              type="password"
              autoComplete="new-password"
              required
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent transition-colors"
            />
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-white font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            aria-busy={submitting}
          >
            {submitting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" /> Enregistrement...
              </>
            ) : (
              'Mettre a jour le mot de passe'
            )}
          </button>
        </form>
      </section>
    </div>
  );
}
