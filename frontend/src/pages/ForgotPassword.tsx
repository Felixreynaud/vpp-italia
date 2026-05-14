import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Activity, AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';
import { requestPasswordReset } from '../api/client';

export function ForgotPassword() {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await requestPasswordReset(email.trim().toLowerCase());
      setSent(true);
    } catch {
      // Anti-enumeration: backend always returns 200. If we hit an error here
      // it's a network issue — still pretend success to keep UX consistent.
      setSent(true);
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
          <p className="text-sm text-slate-400 mt-1">{t('forgot_password.subtitle')}</p>
        </div>

        <div className="bg-surface rounded-2xl border border-border p-6">
          {sent ? (
            <div className="space-y-4 text-center">
              <div className="flex justify-center">
                <CheckCircle2 className="w-10 h-10 text-success" />
              </div>
              <h2 className="text-lg font-semibold text-white">{t('forgot_password.success_title')}</h2>
              <p className="text-sm text-slate-400">
                {t('forgot_password.success_message', { email })}
              </p>
              <p className="text-xs text-slate-500">{t('forgot_password.expires_hint')}</p>
              <Link
                to="/login"
                className="inline-block text-sm text-primary hover:underline"
              >
                {t('forgot_password.back_to_login')}
              </Link>
            </div>
          ) : (
            <form onSubmit={(e) => { void handleSubmit(e); }} className="space-y-4">
              <h2 className="text-lg font-semibold text-white text-center">{t('forgot_password.title')}</h2>
              <p className="text-sm text-slate-400 text-center">
                {t('forgot_password.instruction')}
              </p>

              {error && (
                <div role="alert" className="flex items-center gap-2 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
                </div>
              )}

              <div className="space-y-1">
                <label htmlFor="email" className="block text-sm font-medium text-slate-300">
                  {t('forgot_password.email_label')}
                </label>
                <input
                  id="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent transition-colors"
                  placeholder={t('forgot_password.email_placeholder')}
                />
              </div>

              <button
                type="submit"
                disabled={submitting}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-white font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                aria-busy={submitting}
              >
                {submitting ? (
                  <><Loader2 className="w-4 h-4 animate-spin" /> {t('forgot_password.submitting')}</>
                ) : (
                  t('forgot_password.submit')
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
