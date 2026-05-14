import { useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Activity, AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';
import { confirmPasswordReset } from '../api/client';

export function ResetPassword() {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get('token') ?? '';

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const validate = (): string | null => {
    if (!token) return t('reset_password.errors.token_missing');
    if (newPassword.length < 10) return t('reset_password.errors.too_short');
    if (!/[A-Z]/.test(newPassword)) return t('reset_password.errors.no_uppercase');
    if (!/[0-9]/.test(newPassword)) return t('reset_password.errors.no_digit');
    if (newPassword !== confirmPassword) return t('reset_password.errors.mismatch');
    return null;
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const err = validate();
    if (err) {
      setError(err);
      return;
    }
    setSubmitting(true);
    try {
      await confirmPasswordReset(token, newPassword);
      setSuccess(true);
      setTimeout(() => { void navigate('/login'); }, 3000);
    } catch (e: unknown) {
      const apiError = e as { response?: { status?: number; data?: { detail?: string } } };
      const status = apiError.response?.status;
      if (status === 400) {
        setError(t('reset_password.errors.invalid_token'));
      } else if (status === 422) {
        setError(t('reset_password.errors.weak'));
      } else {
        setError(t('reset_password.errors.unexpected'));
      }
    } finally {
      setSubmitting(false);
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
          <p className="text-sm text-slate-400 mt-1">{t('reset_password.subtitle')}</p>
        </div>

        <div className="bg-surface rounded-2xl border border-border p-6">
          {success ? (
            <div className="space-y-4 text-center">
              <div className="flex justify-center">
                <CheckCircle2 className="w-10 h-10 text-success" />
              </div>
              <h2 className="text-lg font-semibold text-white">{t('reset_password.success_title')}</h2>
              <p className="text-sm text-slate-400">
                {t('reset_password.success_message')}
              </p>
            </div>
          ) : (
            <form onSubmit={(e) => { void handleSubmit(e); }} className="space-y-4">
              <h2 className="text-lg font-semibold text-white text-center">{t('reset_password.title')}</h2>

              {error && (
                <div role="alert" className="flex items-center gap-2 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
                </div>
              )}

              <div className="space-y-1">
                <label htmlFor="new_password" className="block text-sm font-medium text-slate-300">
                  {t('reset_password.new_password_label')}
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
                <p className="text-xs text-slate-500">{t('reset_password.hint')}</p>
              </div>

              <div className="space-y-1">
                <label htmlFor="confirm_password" className="block text-sm font-medium text-slate-300">
                  {t('reset_password.confirm_label')}
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
                disabled={submitting || !token}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-white font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                aria-busy={submitting}
              >
                {submitting ? (
                  <><Loader2 className="w-4 h-4 animate-spin" /> {t('reset_password.submitting')}</>
                ) : (
                  t('reset_password.submit')
                )}
              </button>

              <p className="text-center">
                <Link to="/login" className="text-sm text-primary hover:underline">
                  {t('forgot_password.back_to_login')}
                </Link>
              </p>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
