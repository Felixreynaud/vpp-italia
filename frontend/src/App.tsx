import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { Optimize } from './pages/Optimize';
import { Batteries } from './pages/Batteries';
import { History } from './pages/History';
import { Login } from './pages/Login';
import { AdminBatteries } from './pages/admin/Batteries';
import { CreateBattery } from './pages/admin/CreateBattery';
import { Portfolio } from './pages/Portfolio';
import { Account } from './pages/Account';
import { ForgotPassword } from './pages/ForgotPassword';
import { ResetPassword } from './pages/ResetPassword';

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('vpp_token');
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route
          path="/*"
          element={
            <RequireAuth>
              <Layout>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/optimize" element={<Optimize />} />
                  <Route path="/batteries" element={<Batteries />} />
                  <Route path="/history" element={<History />} />
                  <Route path="/admin/batteries" element={<AdminBatteries />} />
                  <Route path="/admin/batteries/new" element={<CreateBattery />} />
                  <Route path="/portfolio" element={<Portfolio />} />
                  <Route path="/account" element={<Account />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Layout>
            </RequireAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
