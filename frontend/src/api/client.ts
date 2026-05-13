import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from 'axios';
import type {
  Battery,
  BulkImportItem,
  BulkImportResponse,
  ConfiguredBattery,
  DiscoverResponse,
  FleetMetrics,
  MGPPricesResponse,
  OptimizeResult,
  AutoconsommationRequest,
  ArbitrageRequest,
  StochastiqueRequest,
  DispatchApplyRequest,
  DispatchApplyResponse,
  BatteryCommandRequest,
  OptimizeScenarioInfo,
  LoginRequest,
  LoginResponse,
  HistoryPoint,
  DispatchSession,
  TestConnectionResponse,
} from './types';

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
const MOCK_DATA = import.meta.env.VITE_MOCK_DATA === 'true';

const axiosInstance: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
});

axiosInstance.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem('vpp_token');
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

axiosInstance.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      localStorage.removeItem('vpp_token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

function generateMockBatteries(): Battery[] {
  const states: Battery['state'][] = ['idle', 'charging', 'discharging', 'fault', 'offline', 'safe_state'];
  const sites = ['SITE-MI-01', 'SITE-RO-01', 'SITE-NA-01', 'SITE-TO-01'];
  const protocols: Battery['protocol'][] = ['modbus', 'ocpp', 'rest'];

  return Array.from({ length: 24 }, (_, i) => {
    const state = i === 3 ? 'fault' : i === 7 ? 'offline' : states[i % 4];
    const soc = 15 + Math.random() * 70;
    const power = state === 'charging' ? 50 + Math.random() * 200
      : state === 'discharging' ? -(50 + Math.random() * 200)
      : 0;
    return {
      battery_id: `bat-${String(i + 1).padStart(3, '0')}`,
      asset_id: `UPCA-IT-${String(1000 + i)}`,
      site_id: sites[i % sites.length],
      capacity_kwh: 500 + (i % 3) * 250,
      max_power_kw: 250,
      protocol: protocols[i % 3],
      soc_percent: parseFloat(soc.toFixed(1)),
      power_kw: parseFloat(power.toFixed(1)),
      state,
      temperature_c: 22 + Math.random() * 15,
      voltage_v: 380 + Math.random() * 20,
      last_seen: new Date().toISOString(),
      manufacturer: i % 2 === 0 ? 'Huawei LUNA2000' : 'BYD Battery-Box',
    };
  });
}

const MOCK_BATTERIES = generateMockBatteries();

function getMockFleetMetrics(): FleetMetrics {
  const active = MOCK_BATTERIES.filter((b) => !['fault', 'offline'].includes(b.state));
  const socMoyen = active.reduce((s, b) => s + b.soc_percent, 0) / active.length;
  const totalPower = active.reduce((s, b) => s + b.power_kw, 0);
  return {
    soc_moyen: parseFloat(socMoyen.toFixed(1)),
    puissance_totale_kw: parseFloat(totalPower.toFixed(1)),
    batteries_actives: active.length,
    batteries_total: MOCK_BATTERIES.length,
    energie_disponible_mwh: parseFloat(((socMoyen / 100) * 12000).toFixed(1)),
    pnl_jour_eur: 4280 + Math.random() * 500,
  };
}

function getMockMGPPrices(): MGPPricesResponse {
  const base = [45, 42, 40, 38, 37, 38, 50, 75, 90, 85, 78, 72, 68, 65, 70, 80, 95, 110, 105, 92, 78, 65, 55, 48];
  return {
    prices: base.map((p, i) => ({
      hour: i,
      price_eur_mwh: parseFloat((p + (Math.random() - 0.5) * 5).toFixed(2)),
    })),
  };
}

function getMockOptimizeResult(scenario: string): OptimizeResult {
  const schedule = Array.from({ length: 24 }, (_, i) => {
    let power = 0;
    if (i >= 2 && i <= 6) power = -(150 + Math.random() * 100);
    else if (i >= 8 && i <= 11) power = 100 + Math.random() * 150;
    else if (i >= 17 && i <= 21) power = 180 + Math.random() * 200;
    return { hour: i, power_kw: parseFloat(power.toFixed(1)) };
  });
  return {
    schedule,
    revenus_estimes_eur: 3200 + Math.random() * 800,
    taux_autoconsommation_pct: scenario === 'autoconsommation' ? 78 + Math.random() * 15 : undefined,
    sharpe_ratio: scenario === 'stochastique' ? 1.8 + Math.random() * 0.5 : undefined,
    cvar: scenario === 'stochastique' ? -(200 + Math.random() * 100) : undefined,
    scenario: scenario as OptimizeResult['scenario'],
  };
}

function getMockHistory(): HistoryPoint[] {
  const now = Date.now();
  return Array.from({ length: 168 }, (_, i) => {
    const ts = new Date(now - (167 - i) * 3600000).toISOString();
    const hour = new Date(ts).getHours();
    const discharge = hour >= 8 && hour <= 22 ? 200 + Math.random() * 300 : 0;
    const charge = hour >= 1 && hour <= 6 ? 150 + Math.random() * 200 : 0;
    return {
      timestamp: ts,
      power_charge_kw: parseFloat(charge.toFixed(1)),
      power_discharge_kw: parseFloat(discharge.toFixed(1)),
      soc_moyen: 40 + Math.sin(i / 10) * 20 + Math.random() * 5,
      pnl_cumul_eur: i * 28 + Math.random() * 20,
    };
  });
}

function getMockDispatchSessions(): DispatchSession[] {
  const markets = ['MSD', 'MGP', 'MI1', 'MI3', 'MB'];
  return Array.from({ length: 20 }, (_, i) => ({
    id: `sess-${String(i + 1).padStart(4, '0')}`,
    date: new Date(Date.now() - i * 86400000).toISOString(),
    duration_min: 15 + Math.floor(Math.random() * 105),
    energie_mwh: parseFloat((0.5 + Math.random() * 4.5).toFixed(2)),
    revenu_eur: parseFloat((80 + Math.random() * 420).toFixed(2)),
    marche: markets[i % markets.length],
  }));
}

export async function login(req: LoginRequest): Promise<LoginResponse> {
  if (MOCK_DATA) {
    const token = btoa(`mock:${req.username}:${Date.now()}`);
    return { access_token: token, token_type: 'bearer' };
  }
  const { data } = await axiosInstance.post<LoginResponse>('/api/v1/auth/login', req);
  return data;
}

export async function fetchBatteries(): Promise<Battery[]> {
  if (MOCK_DATA) return MOCK_BATTERIES;
  const { data } = await axiosInstance.get<{ data: Battery[] }>('/api/v1/batteries');
  return data.data;
}

export async function fetchFleetMetrics(): Promise<FleetMetrics> {
  if (MOCK_DATA) return getMockFleetMetrics();
  const { data } = await axiosInstance.get<{ data: FleetMetrics }>('/api/v1/metrics/fleet');
  return data.data;
}

export async function fetchMGPPrices(): Promise<MGPPricesResponse> {
  if (MOCK_DATA) return getMockMGPPrices();
  const { data } = await axiosInstance.get<{ data: MGPPricesResponse }>('/api/v1/markets/mgp/prices');
  return data.data;
}

export async function fetchOptimizeScenarios(): Promise<OptimizeScenarioInfo[]> {
  if (MOCK_DATA) {
    return [
      { id: 'autoconsommation', name: 'Autoconsommation', description: "Maximise l'autoconsommation PV" },
      { id: 'arbitrage', name: 'Arbitrage MGP', description: 'Arbitrage sur le marche spot' },
      { id: 'stochastique', name: 'Stochastique', description: 'Optimisation avec incertitude de prix' },
    ];
  }
  const { data } = await axiosInstance.get<{ data: OptimizeScenarioInfo[] }>('/api/v1/optimize/scenarios');
  return data.data;
}

export async function runOptimizeAutoconsommation(req: AutoconsommationRequest): Promise<OptimizeResult> {
  if (MOCK_DATA) return getMockOptimizeResult('autoconsommation');
  const { data } = await axiosInstance.post<{ data: OptimizeResult }>('/api/v1/optimize/autoconsommation', req);
  return data.data;
}

export async function runOptimizeArbitrage(req: ArbitrageRequest): Promise<OptimizeResult> {
  if (MOCK_DATA) return getMockOptimizeResult('arbitrage');
  const { data } = await axiosInstance.post<{ data: OptimizeResult }>('/api/v1/optimize/arbitrage', req);
  return data.data;
}

export async function runOptimizeStochastique(req: StochastiqueRequest): Promise<OptimizeResult> {
  if (MOCK_DATA) return getMockOptimizeResult('stochastique');
  const { data } = await axiosInstance.post<{ data: OptimizeResult }>('/api/v1/optimize/stochastique', req);
  return data.data;
}

export async function applyDispatch(req: DispatchApplyRequest): Promise<DispatchApplyResponse> {
  if (MOCK_DATA) {
    return { success: true, message: 'Planning applique avec succes', applied_at: new Date().toISOString() };
  }
  const { data } = await axiosInstance.post<{ data: DispatchApplyResponse }>('/api/v1/dispatch/apply', req);
  return data.data;
}

export async function sendBatteryCommand(
  batteryId: string,
  req: BatteryCommandRequest
): Promise<void> {
  if (MOCK_DATA) return;

  // Convention backend (DispatchCommand): power_kw > 0 = discharge, < 0 = charge, 0 = stop.
  // We translate from the UI's intent + power magnitude.
  let power_kw: number;
  if (req.command === 'charge') {
    power_kw = -Math.abs(req.power_kw ?? 0);
  } else if (req.command === 'discharge') {
    power_kw = +Math.abs(req.power_kw ?? 0);
  } else {
    power_kw = 0;
  }

  await axiosInstance.post(`/api/v1/batteries/${batteryId}/dispatch`, {
    power_kw,
    duration_minutes: 15,
    reason: `manual ${req.command} from UI`,
  });
}

export async function fetchHistory(): Promise<HistoryPoint[]> {
  if (MOCK_DATA) return getMockHistory();
  const { data } = await axiosInstance.get<{ data: HistoryPoint[] }>('/api/v1/history');
  return data.data;
}

export async function fetchDispatchSessions(): Promise<DispatchSession[]> {
  if (MOCK_DATA) return getMockDispatchSessions();
  const { data } = await axiosInstance.get<{ data: DispatchSession[] }>('/api/v1/history/sessions');
  return data.data;
}

// ---------------------------------------------------------------------------
// Admin — fleet management (no MOCK fallback, talks to real backend)
// ---------------------------------------------------------------------------

export async function listConfiguredBatteries(): Promise<ConfiguredBattery[]> {
  const { data } = await axiosInstance.get<{ data: ConfiguredBattery[] }>('/api/v1/batteries');
  return data.data;
}

export async function discoverHuawei(
  endpointUrl: string,
  clientId: string,
  clientSecret: string
): Promise<DiscoverResponse> {
  const { data } = await axiosInstance.post<DiscoverResponse>(
    '/api/v1/batteries/discover/huawei',
    { endpoint_url: endpointUrl, client_id: clientId, client_secret: clientSecret }
  );
  return data;
}

export async function bulkImportBatteries(
  endpointUrl: string,
  clientId: string,
  clientSecret: string,
  batteries: BulkImportItem[]
): Promise<BulkImportResponse> {
  const { data } = await axiosInstance.post<BulkImportResponse>(
    '/api/v1/batteries/bulk-import',
    {
      endpoint_url: endpointUrl,
      client_id: clientId,
      client_secret: clientSecret,
      batteries,
    }
  );
  return data;
}

export async function deleteBattery(batteryId: string): Promise<void> {
  await axiosInstance.delete(`/api/v1/batteries/${batteryId}`);
}

export async function testBatteryConnection(
  batteryId: string
): Promise<TestConnectionResponse> {
  const { data } = await axiosInstance.post<TestConnectionResponse>(
    `/api/v1/batteries/${batteryId}/test-connection`
  );
  return data;
}

export default axiosInstance;
