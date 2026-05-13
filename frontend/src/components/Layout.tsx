import { useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import {
  LayoutDashboard,
  Zap,
  Battery,
  BarChart2,
  LogOut,
  Menu,
  X,
  Activity,
  Settings,
  FolderOpen,
} from 'lucide-react';
import { useCETClock } from '../hooks/useCETClock';

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/optimize', label: 'Optimisation', icon: Zap },
  { to: '/batteries', label: 'Activations batteries', icon: Battery },
  { to: '/history', label: 'Historique', icon: BarChart2 },
  { to: '/admin/batteries', label: 'Management batterie', icon: Settings },
  { to: '/portfolio', label: 'Portefeuille batteries', icon: FolderOpen },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const navigate = useNavigate();
  const cetTime = useCETClock();

  const handleLogout = () => {
    localStorage.removeItem('vpp_token');
    void navigate('/login');
  };

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/60 lg:hidden"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}
      <aside
        className={`
          fixed inset-y-0 left-0 z-30 w-64 bg-surface border-r border-border
          transform transition-transform duration-200 ease-in-out
          lg:static lg:translate-x-0
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        `}
        aria-label="Navigation principale"
      >
        <div className="flex items-center gap-3 px-6 py-5 border-b border-border">
          <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-primary/20">
            <Activity className="w-5 h-5 text-primary" />
          </div>
          <div>
            <span className="font-bold text-white text-lg leading-none">VPP Italia</span>
            <p className="text-xs text-slate-400 mt-0.5">Pannello Operatore</p>
          </div>
          <button
            className="ml-auto lg:hidden text-slate-400 hover:text-white"
            onClick={() => setSidebarOpen(false)}
            aria-label="Fermer le menu"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1" aria-label="Pages">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors
                ${isActive
                  ? 'bg-primary/20 text-primary'
                  : 'text-slate-400 hover:bg-slate-700/50 hover:text-white'
                }`
              }
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-3 py-4 border-t border-border">
          <button
            onClick={handleLogout}
            className="flex w-full items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-slate-400 hover:bg-slate-700/50 hover:text-white transition-colors"
            aria-label="Se deconnecter"
          >
            <LogOut className="w-4 h-4 flex-shrink-0" />
            Deconnexion
          </button>
        </div>
      </aside>
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <header className="flex items-center justify-between px-4 lg:px-6 py-3 bg-surface border-b border-border">
          <button
            className="lg:hidden text-slate-400 hover:text-white"
            onClick={() => setSidebarOpen(true)}
            aria-label="Ouvrir le menu"
          >
            <Menu className="w-5 h-5" />
          </button>
          <div className="flex items-center gap-2 ml-auto">
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <div className="w-2 h-2 rounded-full bg-success animate-pulse" aria-hidden="true" />
              <span className="hidden sm:inline">CET</span>
              <time dateTime={cetTime} className="font-mono text-white font-medium">{cetTime}</time>
            </div>
          </div>
        </header>
        <main className="flex-1 overflow-y-auto scrollbar-thin p-4 lg:p-6" role="main">
          {children}
        </main>
      </div>
    </div>
  );
}
