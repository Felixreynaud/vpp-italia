export type BatteryState =
  | 'idle'
  | 'charging'
  | 'discharging'
  | 'fault'
  | 'offline'
  | 'safe_state';

export type BatteryProtocol = 'modbus' | 'ocpp' | 'rest';

export interface Battery {
  battery_id: string;
  asset_id: string;
  site_id: string;
  capacity_kwh: number;
  max_power_kw: number;
  protocol: BatteryProtocol;
  soc_percent: number;
  power_kw: number;
  state: BatteryState;
  temperature_c: number;
  voltage_v?: number;
  last_seen?: string;
  manufacturer?: string;
}

export interface FleetMetrics {
  soc_moyen: number;
  puissance_totale_kw: number;
  batteries_actives: number;
  batteries_total: number;
  energie_disponible_mwh: number;
  pnl_jour_eur?: number;
}

export interface MGPPrice {
  hour: number;
  price_eur_mwh: number;
}

export interface MGPPricesResponse {
  prices: MGPPrice[];
}

export type OptimizeScenario = 'autoconsommation' | 'arbitrage' | 'stochastique';
export type ArbitrageMode = 'conservateur' | 'standard' | 'agressif';

export interface AutoconsommationRequest {
  site_id: string;
  production_pv_kw: number[];
  consommation_kw: number[];
  prix_mgp: number[];
}

export interface ArbitrageRequest {
  site_id: string;
  prix_mgp: number[];
  mode: ArbitrageMode;
}

export interface StochastiqueRequest {
  site_id: string;
  prix_mgp_base: number[];
  incertitude_pct: number;
}

export interface OptimizeResult {
  schedule: ScheduleSlot[];
  revenus_estimes_eur: number;
  taux_autoconsommation_pct?: number;
  sharpe_ratio?: number;
  cvar?: number;
  scenario: OptimizeScenario;
}

export interface ScheduleSlot {
  hour: number;
  power_kw: number;
}

export interface OptimizeScenarioInfo {
  id: string;
  name: string;
  description: string;
}

export interface DispatchApplyRequest {
  schedule: ScheduleSlot[];
}

export interface DispatchApplyResponse {
  success: boolean;
  message: string;
  applied_at: string;
}

export type BatteryCommand = 'charge' | 'discharge' | 'stop';

export interface BatteryCommandRequest {
  command: BatteryCommand;
  power_kw?: number;
}

export interface HistoryPoint {
  timestamp: string;
  power_charge_kw: number;
  power_discharge_kw: number;
  soc_moyen: number;
  pnl_cumul_eur: number;
}

export interface DispatchSession {
  id: string;
  date: string;
  duration_min: number;
  energie_mwh: number;
  revenu_eur: number;
  marche: string;
}

// ---------------------------------------------------------------------------
// Admin — configured batteries (DB-side, distinct from the runtime Battery type)
// ---------------------------------------------------------------------------

export interface ConfiguredBattery {
  battery_id: string;
  asset_id: string;
  site_id: string;
  name: string;
  protocol: BatteryProtocol;
  host: string;
  port: number;
  capacity_kwh: string;
  max_power_kw: string;
  min_soc_percent: string;
  max_soc_percent: string;
  ramp_rate_kw_per_min: string | null;
  state: BatteryState;
  is_active: boolean;
  metadata_?: {
    subtype?: string;
    endpoint_url?: string;
    plant_code?: string;
    device_id?: string;
    model?: string;
    client_id?: string;
    client_secret?: string;
  } | null;
  created_at: string;
  updated_at: string;
}

export interface DiscoveredBattery {
  plant_code: string;
  plant_name: string;
  device_id: string;
  model: string | null;
  capacity_kwh: string;
  max_power_kw: string;
}

export interface DiscoverResponse {
  data: DiscoveredBattery[];
  meta: { count: number; endpoint: string };
}

export interface BulkImportItem {
  asset_id: string;
  site_id: string;
  name: string;
  plant_code: string;
  device_id: string;
  model: string | null;
  capacity_kwh: string;
  max_power_kw: string;
}

export interface BulkImportResponse {
  imported: number;
  skipped: number;
  battery_ids: string[];
}

export interface TestConnectionResponse {
  ok: boolean;
  error?: string;
  soc_percent?: number;
  power_kw?: number;
  voltage_v?: number;
  temperature_c?: number;
  soh?: number;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
}

export interface ApiResponse<T> {
  data: T;
  meta: {
    timestamp: string;
    [key: string]: unknown;
  };
}
