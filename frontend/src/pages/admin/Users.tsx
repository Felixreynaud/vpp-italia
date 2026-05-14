import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Mail,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserCog,
  UserPlus,
  Users,
} from 'lucide-react';
import {
  deleteUser,
  inviteUser,
  listUsers,
  resendInvite,
  updateUser,
} from '../../api/client';
import type { AdminUser, UserRole } from '../../api/types';
import { useAuth } from '../../hooks/useAuth';

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('fr-FR', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function extractError(e: unknown, fallback: string): string {
  const err = e as { response?: { status?: number; data?: { detail?: string } } };
  return err.response?.data?.detail ?? fallback;
}

export function AdminUsers() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  const [inviteOpen, setInviteOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<AdminUser | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listUsers();
      setUsers(data);
    } catch (e) {
      setError(extractError(e, 'Impossible de charger les utilisateurs.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), 4000);
    return () => clearTimeout(t);
  }, [flash]);

  const onInviteSuccess = async () => {
    setInviteOpen(false);
    setFlash({ kind: 'success', text: 'Invitation envoyee.' });
    await load();
  };

  const onToggleActive = async (u: AdminUser) => {
    try {
      await updateUser(u.user_id, { is_active: !u.is_active });
      setFlash({
        kind: 'success',
        text: u.is_active ? 'Utilisateur desactive.' : 'Utilisateur active.',
      });
      await load();
    } catch (e) {
      setFlash({ kind: 'error', text: extractError(e, 'Operation impossible.') });
    }
  };

  const onChangeRole = async (u: AdminUser, role: UserRole) => {
    if (u.role === role) return;
    try {
      await updateUser(u.user_id, { role });
      setFlash({ kind: 'success', text: 'Role mis a jour.' });
      await load();
    } catch (e) {
      setFlash({ kind: 'error', text: extractError(e, 'Operation impossible.') });
    }
  };

  const onResendInvite = async (u: AdminUser) => {
    try {
      await resendInvite(u.user_id);
      setFlash({ kind: 'success', text: `Invitation renvoyee a ${u.email}.` });
    } catch (e) {
      setFlash({ kind: 'error', text: extractError(e, 'Envoi impossible.') });
    }
  };

  const onDeleteConfirmed = async () => {
    if (!confirmDelete) return;
    try {
      await deleteUser(confirmDelete.user_id);
      setFlash({ kind: 'success', text: 'Utilisateur supprime.' });
      setConfirmDelete(null);
      await load();
    } catch (e) {
      setFlash({ kind: 'error', text: extractError(e, 'Suppression impossible.') });
      setConfirmDelete(null);
    }
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-primary/20">
            <Users className="w-5 h-5 text-primary" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-white">Utilisateurs</h1>
            <p className="text-sm text-slate-400">Gerez les acces a la plateforme.</p>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => { void load(); }}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface border border-border text-slate-300 hover:bg-slate-700/50 transition-colors"
            aria-label="Rafraichir"
          >
            <RefreshCw className="w-4 h-4" /> Rafraichir
          </button>
          <button
            onClick={() => setInviteOpen(true)}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-primary text-white hover:bg-primary/90 transition-colors"
          >
            <Plus className="w-4 h-4" /> Inviter
          </button>
        </div>
      </header>

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

      <div className="bg-surface rounded-2xl border border-border overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center p-12 text-slate-400">
            <Loader2 className="w-5 h-5 animate-spin mr-2" /> Chargement...
          </div>
        ) : error ? (
          <div className="p-6 flex items-center gap-2 text-danger">
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        ) : users.length === 0 ? (
          <div className="p-12 text-center text-slate-400 text-sm">Aucun utilisateur.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-background/50 text-slate-400 text-left">
                <tr>
                  <th className="px-4 py-3 font-medium">Email</th>
                  <th className="px-4 py-3 font-medium">Nom</th>
                  <th className="px-4 py-3 font-medium">Role</th>
                  <th className="px-4 py-3 font-medium">Statut</th>
                  <th className="px-4 py-3 font-medium">Derniere connexion</th>
                  <th className="px-4 py-3 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {users.map((u) => {
                  const isSelf = currentUser?.user_id === u.user_id;
                  const pendingInvite = !u.is_active && !u.email_verified_at;
                  return (
                    <tr key={u.user_id} className="hover:bg-slate-700/30 transition-colors">
                      <td className="px-4 py-3 text-white font-medium">
                        {u.email}
                        {isSelf && (
                          <span className="ml-2 inline-block text-xs text-slate-500">(vous)</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-slate-300">{u.full_name}</td>
                      <td className="px-4 py-3">
                        <select
                          value={u.role}
                          onChange={(e) => { void onChangeRole(u, e.target.value as UserRole); }}
                          disabled={isSelf}
                          aria-label={`Role pour ${u.email}`}
                          className="bg-background border border-border rounded px-2 py-1 text-sm text-white disabled:opacity-50"
                        >
                          <option value="admin">Admin</option>
                          <option value="operator">Operator</option>
                        </select>
                      </td>
                      <td className="px-4 py-3">
                        {pendingInvite ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-warning/10 border border-warning/30 text-warning text-xs">
                            <Mail className="w-3 h-3" /> Invitation envoyee
                          </span>
                        ) : u.is_active ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-success/10 border border-success/30 text-success text-xs">
                            <ShieldCheck className="w-3 h-3" /> Actif
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-700/50 border border-border text-slate-400 text-xs">
                            Desactive
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-slate-400">{formatDate(u.last_login_at)}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1 justify-end">
                          {pendingInvite && (
                            <button
                              onClick={() => { void onResendInvite(u); }}
                              className="p-1.5 rounded text-slate-400 hover:bg-slate-700 hover:text-white"
                              title="Renvoyer l'invitation"
                            >
                              <Mail className="w-4 h-4" />
                            </button>
                          )}
                          <button
                            onClick={() => { void onToggleActive(u); }}
                            disabled={isSelf}
                            className="p-1.5 rounded text-slate-400 hover:bg-slate-700 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
                            title={u.is_active ? 'Desactiver' : 'Activer'}
                          >
                            <UserCog className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => setConfirmDelete(u)}
                            disabled={isSelf}
                            className="p-1.5 rounded text-slate-400 hover:bg-danger/20 hover:text-danger disabled:opacity-30 disabled:cursor-not-allowed"
                            title="Supprimer"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {inviteOpen && (
        <InviteModal
          onClose={() => setInviteOpen(false)}
          onSuccess={() => { void onInviteSuccess(); }}
        />
      )}

      {confirmDelete && (
        <ConfirmDeleteModal
          user={confirmDelete}
          onCancel={() => setConfirmDelete(null)}
          onConfirm={() => { void onDeleteConfirmed(); }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Invite modal
// ---------------------------------------------------------------------------

function InviteModal({ onClose, onSuccess }: { onClose: () => void; onSuccess: () => void }) {
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');
  const [role, setRole] = useState<UserRole>('operator');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await inviteUser({ email: email.trim().toLowerCase(), full_name: fullName.trim(), role });
      onSuccess();
    } catch (e) {
      setError(extractError(e, 'Invitation impossible.'));
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
            <UserPlus className="w-5 h-5 text-primary" />
          </div>
          <h2 className="text-lg font-semibold text-white">Inviter un utilisateur</h2>
        </div>

        <form onSubmit={(e) => { void handleSubmit(e); }} className="space-y-4">
          {error && (
            <div role="alert" className="flex items-center gap-2 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
              <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
            </div>
          )}

          <div className="space-y-1">
            <label htmlFor="invite_email" className="block text-sm font-medium text-slate-300">Email</label>
            <input
              id="invite_email" type="email" required
              value={email} onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white focus:outline-none focus:ring-2 focus:ring-primary"
              placeholder="utilisateur@exemple.com"
            />
          </div>

          <div className="space-y-1">
            <label htmlFor="invite_name" className="block text-sm font-medium text-slate-300">Nom complet</label>
            <input
              id="invite_name" type="text" required
              value={fullName} onChange={(e) => setFullName(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white focus:outline-none focus:ring-2 focus:ring-primary"
              placeholder="Jean Dupont"
            />
          </div>

          <div className="space-y-1">
            <label htmlFor="invite_role" className="block text-sm font-medium text-slate-300">Role</label>
            <select
              id="invite_role" value={role}
              onChange={(e) => setRole(e.target.value as UserRole)}
              className="w-full px-3 py-2.5 rounded-lg bg-background border border-border text-white focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="operator">Operator (acces a tout sauf gestion des utilisateurs)</option>
              <option value="admin">Admin (acces complet)</option>
            </select>
          </div>

          <p className="text-xs text-slate-500">
            L'utilisateur recevra un email avec un lien pour definir son mot de passe (valide 7 jours).
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
              {submitting ? <><Loader2 className="w-4 h-4 animate-spin" /> Envoi...</> : 'Envoyer l\'invitation'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation modal
// ---------------------------------------------------------------------------

function ConfirmDeleteModal({
  user, onCancel, onConfirm,
}: {
  user: AdminUser;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-40 bg-black/60 flex items-center justify-center p-4"
      onClick={onCancel}
    >
      <div
        className="bg-surface rounded-2xl border border-border max-w-md w-full p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 mb-4">
          <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-danger/20">
            <Trash2 className="w-5 h-5 text-danger" />
          </div>
          <h2 className="text-lg font-semibold text-white">Supprimer l'utilisateur ?</h2>
        </div>
        <p className="text-sm text-slate-400 mb-6">
          L'utilisateur <strong className="text-white">{user.email}</strong> sera supprime
          definitivement. Cette action est irreversible.
        </p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg bg-slate-700 text-white hover:bg-slate-600 transition-colors"
          >
            Annuler
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 rounded-lg bg-danger text-white hover:bg-danger/90 transition-colors"
          >
            Supprimer
          </button>
        </div>
      </div>
    </div>
  );
}
