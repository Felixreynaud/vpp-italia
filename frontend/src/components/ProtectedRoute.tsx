import { Navigate } from 'react-router-dom';
import { Loader2, ShieldOff } from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import type { UserRole } from '../api/types';

interface Props {
  children: React.ReactNode;
  requireRole?: UserRole;
}

export function ProtectedRoute({ children, requireRole }: Props) {
  const { user, loading } = useAuth();
  const token = localStorage.getItem('vpp_token');

  if (!token) return <Navigate to="/login" replace />;

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[40vh] text-slate-400">
        <Loader2 className="w-5 h-5 animate-spin mr-2" /> Verification...
      </div>
    );
  }

  if (!user) return <Navigate to="/login" replace />;

  if (requireRole && user.role !== requireRole) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-center p-6">
        <ShieldOff className="w-10 h-10 text-danger mb-3" />
        <h2 className="text-xl font-semibold text-white">Acces refuse</h2>
        <p className="text-sm text-slate-400 mt-1 max-w-md">
          Cette page est reservee aux administrateurs.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
