import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ChevronLeft,
  ChevronRight,
  Check,
  AlertCircle,
  Loader2,
  IdCard,
  Cpu,
  Sliders,
  MapPin,
  Briefcase,
  Sun,
  TrendingUp,
  Wrench,
  Shield,
} from 'lucide-react';
import {
  createBattery,
  fetchBatteryModels,
} from '../../api/client';
import type {
  BatteryMetadata,
  BatteryModelInfo,
  CreateBatteryRequest,
  GmeZone,
} from '../../api/types';

// ---------------------------------------------------------------------------
// Form state — flat shape, we re-pack into nested metadata on submit
// ---------------------------------------------------------------------------

interface FormState {
  // Top-level (DB columns)
  asset_id: string;
  name: string;
  capacity_kwh: number;
  max_power_kw: number;
  min_soc_percent: number;
  max_soc_percent: number;
  ramp_rate_kw_per_min: number | null;
  // Connection
  endpoint_url: string;
  client_id: string;
  client_secret: string;
  // 1. Identity
  model: string;
  serial_number: string;
  manufacturer: string;
  installation_date: string;
  warranty_end_date: string;
  // 2. Tech specs (pre-filled from model)
  chemistry: string;
  nominal_voltage_v: number;
  cycles_guaranteed: number;
  soh_initial_pct: number;
  round_trip_efficiency_pct: number;
  temp_min_c: number;
  temp_max_c: number;
  // 3. Operational
  target_idle_soc_pct: number;
  default_mode: 'idle' | 'auto' | 'manual';
  dispatch_priority: number;
  // 4. Location
  address: string;
  city: string;
  postal_code: string;
  region: string;
  gme_zone: GmeZone;
  latitude: number | null;
  longitude: number | null;
  pod_code: string;
  dso: string;
  // 5. Customer
  customer_name: string;
  customer_vat: string;
  customer_email: string;
  customer_phone: string;
  contract_type: 'BSP' | 'lease' | 'autoconsommation' | 'hybride';
  contract_start_date: string;
  contract_end_date: string;
  revenue_share_pct: number;
  // 6. Production
  has_pv: boolean;
  pv_capacity_kwc: number;
  site_type: 'residential' | 'commercial' | 'industrial';
  annual_consumption_mwh: number;
  peak_demand_kw: number;
  // 7. Market
  eligible_mgp: boolean;
  eligible_msd: boolean;
  eligible_mb: boolean;
  default_strategy: 'autoconsommation' | 'arbitrage' | 'stochastique';
  min_sell_price_eur_mwh: number;
  max_buy_price_eur_mwh: number;
  risk_tolerance: number;
  // 8. Maintenance
  last_maintenance_date: string;
  next_maintenance_due: string;
  maintenance_contract_id: string;
  criticality_level: 'low' | 'medium' | 'high';
  // 9. Compliance
  gaudi_code: string;
  cei_certification: string;
  last_compliance_test_date: string;
  data_retention_audit_url: string;
}

const TODAY = new Date().toISOString().slice(0, 10);

const DEFAULT_STATE: FormState = {
  asset_id: '',
  name: '',
  capacity_kwh: 0,
  max_power_kw: 0,
  min_soc_percent: 10,
  max_soc_percent: 90,
  ramp_rate_kw_per_min: null,
  endpoint_url: 'http://127.0.0.1:9999',
  client_id: 'sim',
  client_secret: 'sim',
  model: '',
  serial_number: '',
  manufacturer: 'Huawei',
  installation_date: TODAY,
  warranty_end_date: '',
  chemistry: 'LFP',
  nominal_voltage_v: 0,
  cycles_guaranteed: 0,
  soh_initial_pct: 100,
  round_trip_efficiency_pct: 92,
  temp_min_c: -20,
  temp_max_c: 55,
  target_idle_soc_pct: 50,
  default_mode: 'auto',
  dispatch_priority: 5,
  address: '',
  city: '',
  postal_code: '',
  region: '',
  gme_zone: 'NORD',
  latitude: null,
  longitude: null,
  pod_code: '',
  dso: 'e-Distribuzione',
  customer_name: '',
  customer_vat: '',
  customer_email: '',
  customer_phone: '',
  contract_type: 'BSP',
  contract_start_date: TODAY,
  contract_end_date: '',
  revenue_share_pct: 20,
  has_pv: false,
  pv_capacity_kwc: 0,
  site_type: 'commercial',
  annual_consumption_mwh: 0,
  peak_demand_kw: 0,
  eligible_mgp: true,
  eligible_msd: false,
  eligible_mb: false,
  default_strategy: 'arbitrage',
  min_sell_price_eur_mwh: 30,
  max_buy_price_eur_mwh: 100,
  risk_tolerance: 5,
  last_maintenance_date: '',
  next_maintenance_due: '',
  maintenance_contract_id: '',
  criticality_level: 'medium',
  gaudi_code: '',
  cei_certification: 'CEI 0-16',
  last_compliance_test_date: '',
  data_retention_audit_url: '',
};

// Derive a stable UUID from a string (deterministic, useful for site_id from city/zone)
function deriveUuidFromString(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash + str.charCodeAt(i)) | 0;
  }
  const h = Math.abs(hash).toString(16).padStart(12, '0').slice(0, 12);
  return `00000000-0000-0000-0000-${h}`;
}

const STEPS = [
  { id: 'identity', label: 'Identité', icon: IdCard },
  { id: 'tech', label: 'Specs', icon: Cpu },
  { id: 'operational', label: 'Opérationnels', icon: Sliders },
  { id: 'location', label: 'Géographie', icon: MapPin },
  { id: 'customer', label: 'Client', icon: Briefcase },
  { id: 'production', label: 'Production', icon: Sun },
  { id: 'market', label: 'Marché', icon: TrendingUp },
  { id: 'maintenance', label: 'Maintenance', icon: Wrench },
  { id: 'compliance', label: 'Compliance', icon: Shield },
] as const;

type StepId = (typeof STEPS)[number]['id'];

// ---------------------------------------------------------------------------
// Reusable form fields
// ---------------------------------------------------------------------------

interface FieldProps {
  label: string;
  required?: boolean;
  hint?: string;
  children: React.ReactNode;
}

function Field({ label, required, hint, children }: FieldProps) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-slate-400">
        {label}
        {required && <span className="text-red-400 ml-0.5">*</span>}
      </span>
      {children}
      {hint && <span className="text-xs text-slate-500 block">{hint}</span>}
    </label>
  );
}

const baseInputClass =
  'w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:outline-none focus:border-primary';

function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={baseInputClass} />;
}

function NumInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input type="number" step="any" {...props} className={baseInputClass} />;
}

function DateInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input type="date" {...props} className={baseInputClass} />;
}

function SelectInput(
  props: React.SelectHTMLAttributes<HTMLSelectElement> & { children: React.ReactNode }
) {
  return <select {...props} className={baseInputClass} />;
}

function CheckRow({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (b: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="rounded"
      />
      {label}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function CreateBattery() {
  const navigate = useNavigate();
  const [models, setModels] = useState<BatteryModelInfo[]>([]);
  const [form, setForm] = useState<FormState>(DEFAULT_STATE);
  const [currentStep, setCurrentStep] = useState<StepId>('identity');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void fetchBatteryModels().then(setModels).catch(() => setModels([]));
  }, []);

  const set = useCallback(<K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
  }, []);

  // Auto-fill tech specs when model changes
  const onModelChange = (modelName: string) => {
    const m = models.find((mm) => mm.name === modelName);
    if (!m) return;
    setForm((f) => ({
      ...f,
      model: modelName,
      capacity_kwh: m.capacity_kwh,
      max_power_kw: m.max_power_kw,
      chemistry: m.chemistry,
      nominal_voltage_v: m.nominal_voltage_v,
      cycles_guaranteed: m.cycles_guaranteed,
      soh_initial_pct: m.soh_initial_pct,
      round_trip_efficiency_pct: m.round_trip_efficiency_pct,
      temp_min_c: m.temp_min_c,
      temp_max_c: m.temp_max_c,
      site_type: m.tier === 'residential' ? 'residential' : m.tier === 'commercial' ? 'commercial' : 'industrial',
    }));
  };

  const currentIndex = STEPS.findIndex((s) => s.id === currentStep);
  const canSubmit = useMemo(
    () => form.asset_id.trim() !== '' && form.name.trim() !== '' && form.model !== '',
    [form.asset_id, form.name, form.model]
  );

  const handleSubmit = async () => {
    if (!canSubmit) {
      setError('Champs obligatoires manquants : Asset ID, Nom, Modèle');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const site_id = deriveUuidFromString(`${form.gme_zone}-${form.city || 'site'}`);
      const plantCode = `PLANT-${form.asset_id}`;
      const metadata: BatteryMetadata = {
        subtype: 'huawei_fusion_solar',
        endpoint_url: form.endpoint_url,
        plant_code: plantCode,
        device_id: `DEV_${plantCode}`,
        client_id: form.client_id,
        client_secret: form.client_secret,
        model: form.model,
        identity: {
          serial_number: form.serial_number || undefined,
          manufacturer: form.manufacturer,
          model: form.model,
          installation_date: form.installation_date || undefined,
          warranty_end_date: form.warranty_end_date || undefined,
        },
        tech_specs: {
          chemistry: form.chemistry,
          nominal_voltage_v: form.nominal_voltage_v,
          cycles_guaranteed: form.cycles_guaranteed,
          soh_initial_pct: form.soh_initial_pct,
          round_trip_efficiency_pct: form.round_trip_efficiency_pct,
          temp_min_c: form.temp_min_c,
          temp_max_c: form.temp_max_c,
        },
        operational: {
          target_idle_soc_pct: form.target_idle_soc_pct,
          default_mode: form.default_mode,
          dispatch_priority: form.dispatch_priority,
        },
        location: {
          address: form.address || undefined,
          city: form.city || undefined,
          postal_code: form.postal_code || undefined,
          region: form.region || undefined,
          gme_zone: form.gme_zone,
          latitude: form.latitude ?? undefined,
          longitude: form.longitude ?? undefined,
          pod_code: form.pod_code || undefined,
          dso: form.dso || undefined,
        },
        customer: {
          name: form.customer_name || undefined,
          vat_number: form.customer_vat || undefined,
          contact_email: form.customer_email || undefined,
          contact_phone: form.customer_phone || undefined,
          contract_type: form.contract_type,
          contract_start_date: form.contract_start_date || undefined,
          contract_end_date: form.contract_end_date || undefined,
          revenue_share_pct: form.revenue_share_pct,
        },
        production: {
          has_pv: form.has_pv,
          pv_capacity_kwc: form.pv_capacity_kwc,
          site_type: form.site_type,
          annual_consumption_mwh: form.annual_consumption_mwh,
          peak_demand_kw: form.peak_demand_kw,
        },
        market: {
          eligible_mgp: form.eligible_mgp,
          eligible_msd: form.eligible_msd,
          eligible_mb: form.eligible_mb,
          default_strategy: form.default_strategy,
          min_sell_price_eur_mwh: form.min_sell_price_eur_mwh,
          max_buy_price_eur_mwh: form.max_buy_price_eur_mwh,
          risk_tolerance: form.risk_tolerance,
        },
        maintenance: {
          last_maintenance_date: form.last_maintenance_date || undefined,
          next_maintenance_due: form.next_maintenance_due || undefined,
          maintenance_contract_id: form.maintenance_contract_id || undefined,
          criticality_level: form.criticality_level,
        },
        compliance: {
          gaudi_code: form.gaudi_code || undefined,
          cei_certification: form.cei_certification || undefined,
          last_compliance_test_date: form.last_compliance_test_date || undefined,
          data_retention_audit_url: form.data_retention_audit_url || undefined,
        },
      };

      const payload: CreateBatteryRequest = {
        asset_id: form.asset_id,
        site_id,
        name: form.name,
        protocol: 'rest',
        host: '127.0.0.1',
        port: 9999,
        capacity_kwh: form.capacity_kwh,
        max_power_kw: form.max_power_kw,
        min_soc_percent: form.min_soc_percent,
        max_soc_percent: form.max_soc_percent,
        ramp_rate_kw_per_min: form.ramp_rate_kw_per_min,
        metadata_: metadata,
      };
      await createBattery(payload);
      navigate('/admin/batteries');
    } catch (err) {
      setError(
        err instanceof Error
          ? `Erreur création : ${err.message}`
          : 'Erreur création'
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <header className="flex items-center gap-3">
        <button
          onClick={() => navigate('/admin/batteries')}
          className="p-2 rounded-lg bg-slate-700/50 hover:bg-slate-700 text-slate-200"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-white">Nouvelle batterie</h1>
          <p className="text-sm text-slate-400 mt-0.5">
            Crée la batterie dans la VPP + dans le simulateur Huawei.
          </p>
        </div>
      </header>

      {/* Stepper */}
      <nav className="flex gap-1 overflow-x-auto pb-2 border-b border-slate-700">
        {STEPS.map((step, i) => {
          const Icon = step.icon;
          const active = step.id === currentStep;
          return (
            <button
              key={step.id}
              onClick={() => setCurrentStep(step.id)}
              className={`flex items-center gap-2 px-3 py-2 text-xs font-medium whitespace-nowrap rounded-lg transition-colors
                ${active
                  ? 'bg-primary/20 text-primary'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800'
                }`}
            >
              <span className="text-slate-500">{i + 1}.</span>
              <Icon className="w-3.5 h-3.5" />
              {step.label}
            </button>
          );
        })}
      </nav>

      {error && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-300">
          <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
          <span className="text-sm">{error}</span>
        </div>
      )}

      {/* Active step content */}
      <section className="bg-surface rounded-xl border border-slate-700 p-6 space-y-4">
        {currentStep === 'identity' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Asset ID (code UPCA Terna)" required>
              <TextInput
                value={form.asset_id}
                onChange={(e) => set('asset_id', e.target.value)}
                placeholder="UPCA-IT-..."
              />
            </Field>
            <Field label="Nom de la batterie" required>
              <TextInput
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="LUNA2000-30kWh @ Site Milan-01"
              />
            </Field>
            <Field label="Modèle" required hint="Sélectionner pré-remplit les specs techniques">
              <SelectInput
                value={form.model}
                onChange={(e) => onModelChange(e.target.value)}
              >
                <option value="">— Choisir un modèle —</option>
                {models.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name} ({m.capacity_kwh} kWh, {m.max_power_kw} kW, {m.tier})
                  </option>
                ))}
              </SelectInput>
            </Field>
            <Field label="Fabricant">
              <TextInput
                value={form.manufacturer}
                onChange={(e) => set('manufacturer', e.target.value)}
              />
            </Field>
            <Field label="Numéro de série">
              <TextInput
                value={form.serial_number}
                onChange={(e) => set('serial_number', e.target.value)}
              />
            </Field>
            <Field label="Date d'installation">
              <DateInput
                value={form.installation_date}
                onChange={(e) => set('installation_date', e.target.value)}
              />
            </Field>
            <Field label="Fin de garantie">
              <DateInput
                value={form.warranty_end_date}
                onChange={(e) => set('warranty_end_date', e.target.value)}
              />
            </Field>
          </div>
        )}

        {currentStep === 'tech' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Capacité (kWh)" required>
              <NumInput
                value={form.capacity_kwh}
                onChange={(e) => set('capacity_kwh', parseFloat(e.target.value) || 0)}
              />
            </Field>
            <Field label="Puissance max (kW)" required>
              <NumInput
                value={form.max_power_kw}
                onChange={(e) => set('max_power_kw', parseFloat(e.target.value) || 0)}
              />
            </Field>
            <Field label="Chimie">
              <TextInput
                value={form.chemistry}
                onChange={(e) => set('chemistry', e.target.value)}
              />
            </Field>
            <Field label="Tension nominale (V)">
              <NumInput
                value={form.nominal_voltage_v}
                onChange={(e) => set('nominal_voltage_v', parseFloat(e.target.value) || 0)}
              />
            </Field>
            <Field label="Cycles garantis">
              <NumInput
                value={form.cycles_guaranteed}
                onChange={(e) => set('cycles_guaranteed', parseInt(e.target.value, 10) || 0)}
              />
            </Field>
            <Field label="Rendement round-trip (%)">
              <NumInput
                value={form.round_trip_efficiency_pct}
                onChange={(e) =>
                  set('round_trip_efficiency_pct', parseFloat(e.target.value) || 0)
                }
              />
            </Field>
            <Field label="SoH initial (%)">
              <NumInput
                value={form.soh_initial_pct}
                onChange={(e) => set('soh_initial_pct', parseFloat(e.target.value) || 0)}
              />
            </Field>
            <Field label="Température min (°C)">
              <NumInput
                value={form.temp_min_c}
                onChange={(e) => set('temp_min_c', parseFloat(e.target.value) || 0)}
              />
            </Field>
            <Field label="Température max (°C)">
              <NumInput
                value={form.temp_max_c}
                onChange={(e) => set('temp_max_c', parseFloat(e.target.value) || 0)}
              />
            </Field>
            <Field label="Ramp rate (kW/min)">
              <NumInput
                value={form.ramp_rate_kw_per_min ?? ''}
                onChange={(e) =>
                  set('ramp_rate_kw_per_min', e.target.value ? parseFloat(e.target.value) : null)
                }
              />
            </Field>
          </div>
        )}

        {currentStep === 'operational' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="SoC min (%)">
              <NumInput
                value={form.min_soc_percent}
                onChange={(e) => set('min_soc_percent', parseFloat(e.target.value) || 0)}
              />
            </Field>
            <Field label="SoC max (%)">
              <NumInput
                value={form.max_soc_percent}
                onChange={(e) => set('max_soc_percent', parseFloat(e.target.value) || 0)}
              />
            </Field>
            <Field label="SoC repos visé (%)">
              <NumInput
                value={form.target_idle_soc_pct}
                onChange={(e) => set('target_idle_soc_pct', parseFloat(e.target.value) || 0)}
              />
            </Field>
            <Field label="Mode par défaut">
              <SelectInput
                value={form.default_mode}
                onChange={(e) => set('default_mode', e.target.value as FormState['default_mode'])}
              >
                <option value="auto">auto</option>
                <option value="idle">idle</option>
                <option value="manual">manual</option>
              </SelectInput>
            </Field>
            <Field label="Priorité dispatch (1-10)">
              <NumInput
                min={1}
                max={10}
                value={form.dispatch_priority}
                onChange={(e) => set('dispatch_priority', parseInt(e.target.value, 10) || 5)}
              />
            </Field>
          </div>
        )}

        {currentStep === 'location' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Adresse">
              <TextInput
                value={form.address}
                onChange={(e) => set('address', e.target.value)}
              />
            </Field>
            <Field label="Ville">
              <TextInput value={form.city} onChange={(e) => set('city', e.target.value)} />
            </Field>
            <Field label="Code postal">
              <TextInput
                value={form.postal_code}
                onChange={(e) => set('postal_code', e.target.value)}
              />
            </Field>
            <Field label="Région">
              <TextInput
                value={form.region}
                onChange={(e) => set('region', e.target.value)}
                placeholder="Lombardia, Lazio…"
              />
            </Field>
            <Field label="Zone GME" hint="Critique pour avoir les bons prix MGP">
              <SelectInput
                value={form.gme_zone}
                onChange={(e) => set('gme_zone', e.target.value as GmeZone)}
              >
                <option value="NORD">NORD</option>
                <option value="CNOR">CNOR (Centre-Nord)</option>
                <option value="CSUD">CSUD (Centre-Sud)</option>
                <option value="SUD">SUD</option>
                <option value="SARD">SARD (Sardaigne)</option>
                <option value="SICI">SICI (Sicile)</option>
                <option value="CALA">CALA (Calabre)</option>
              </SelectInput>
            </Field>
            <Field label="Latitude">
              <NumInput
                value={form.latitude ?? ''}
                onChange={(e) =>
                  set('latitude', e.target.value ? parseFloat(e.target.value) : null)
                }
              />
            </Field>
            <Field label="Longitude">
              <NumInput
                value={form.longitude ?? ''}
                onChange={(e) =>
                  set('longitude', e.target.value ? parseFloat(e.target.value) : null)
                }
              />
            </Field>
            <Field label="Code POD">
              <TextInput
                value={form.pod_code}
                onChange={(e) => set('pod_code', e.target.value)}
                placeholder="IT001E12345678"
              />
            </Field>
            <Field label="DSO (distributeur)">
              <TextInput
                value={form.dso}
                onChange={(e) => set('dso', e.target.value)}
                placeholder="e-Distribuzione, A2A…"
              />
            </Field>
          </div>
        )}

        {currentStep === 'customer' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Nom client / société">
              <TextInput
                value={form.customer_name}
                onChange={(e) => set('customer_name', e.target.value)}
              />
            </Field>
            <Field label="Partita IVA (VAT)">
              <TextInput
                value={form.customer_vat}
                onChange={(e) => set('customer_vat', e.target.value)}
              />
            </Field>
            <Field label="Email contact">
              <TextInput
                type="email"
                value={form.customer_email}
                onChange={(e) => set('customer_email', e.target.value)}
              />
            </Field>
            <Field label="Téléphone">
              <TextInput
                value={form.customer_phone}
                onChange={(e) => set('customer_phone', e.target.value)}
              />
            </Field>
            <Field label="Type de contrat">
              <SelectInput
                value={form.contract_type}
                onChange={(e) =>
                  set('contract_type', e.target.value as FormState['contract_type'])
                }
              >
                <option value="BSP">BSP (Balance Service Provider)</option>
                <option value="lease">Lease</option>
                <option value="autoconsommation">Autoconsommation</option>
                <option value="hybride">Hybride</option>
              </SelectInput>
            </Field>
            <Field label="Date début contrat">
              <DateInput
                value={form.contract_start_date}
                onChange={(e) => set('contract_start_date', e.target.value)}
              />
            </Field>
            <Field label="Date fin contrat">
              <DateInput
                value={form.contract_end_date}
                onChange={(e) => set('contract_end_date', e.target.value)}
              />
            </Field>
            <Field label="Revenue share (%)">
              <NumInput
                min={0}
                max={100}
                value={form.revenue_share_pct}
                onChange={(e) => set('revenue_share_pct', parseFloat(e.target.value) || 0)}
              />
            </Field>
          </div>
        )}

        {currentStep === 'production' && (
          <div className="space-y-4">
            <CheckRow
              label="Le site a-t-il du PV solaire ?"
              checked={form.has_pv}
              onChange={(v) => set('has_pv', v)}
            />
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {form.has_pv && (
                <Field label="Puissance PV installée (kWc)">
                  <NumInput
                    value={form.pv_capacity_kwc}
                    onChange={(e) => set('pv_capacity_kwc', parseFloat(e.target.value) || 0)}
                  />
                </Field>
              )}
              <Field label="Type de site">
                <SelectInput
                  value={form.site_type}
                  onChange={(e) => set('site_type', e.target.value as FormState['site_type'])}
                >
                  <option value="residential">Résidentiel</option>
                  <option value="commercial">Commercial</option>
                  <option value="industrial">Industriel</option>
                </SelectInput>
              </Field>
              <Field label="Conso annuelle (MWh)">
                <NumInput
                  value={form.annual_consumption_mwh}
                  onChange={(e) => set('annual_consumption_mwh', parseFloat(e.target.value) || 0)}
                />
              </Field>
              <Field label="Pic de demande (kW)">
                <NumInput
                  value={form.peak_demand_kw}
                  onChange={(e) => set('peak_demand_kw', parseFloat(e.target.value) || 0)}
                />
              </Field>
            </div>
          </div>
        )}

        {currentStep === 'market' && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-4">
              <CheckRow
                label="Éligible MGP (marché spot J-1)"
                checked={form.eligible_mgp}
                onChange={(v) => set('eligible_mgp', v)}
              />
              <CheckRow
                label="Éligible MSD (réserve)"
                checked={form.eligible_msd}
                onChange={(v) => set('eligible_msd', v)}
              />
              <CheckRow
                label="Éligible MB (équilibrage)"
                checked={form.eligible_mb}
                onChange={(v) => set('eligible_mb', v)}
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Field label="Stratégie par défaut">
                <SelectInput
                  value={form.default_strategy}
                  onChange={(e) =>
                    set('default_strategy', e.target.value as FormState['default_strategy'])
                  }
                >
                  <option value="autoconsommation">Autoconsommation</option>
                  <option value="arbitrage">Arbitrage MGP</option>
                  <option value="stochastique">Stochastique</option>
                </SelectInput>
              </Field>
              <Field label="Tolérance au risque (1-10)">
                <NumInput
                  min={1}
                  max={10}
                  value={form.risk_tolerance}
                  onChange={(e) => set('risk_tolerance', parseInt(e.target.value, 10) || 5)}
                />
              </Field>
              <Field label="Prix plancher décharge (€/MWh)">
                <NumInput
                  value={form.min_sell_price_eur_mwh}
                  onChange={(e) =>
                    set('min_sell_price_eur_mwh', parseFloat(e.target.value) || 0)
                  }
                />
              </Field>
              <Field label="Prix plafond charge (€/MWh)">
                <NumInput
                  value={form.max_buy_price_eur_mwh}
                  onChange={(e) =>
                    set('max_buy_price_eur_mwh', parseFloat(e.target.value) || 0)
                  }
                />
              </Field>
            </div>
          </div>
        )}

        {currentStep === 'maintenance' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Dernière maintenance">
              <DateInput
                value={form.last_maintenance_date}
                onChange={(e) => set('last_maintenance_date', e.target.value)}
              />
            </Field>
            <Field label="Prochaine maintenance">
              <DateInput
                value={form.next_maintenance_due}
                onChange={(e) => set('next_maintenance_due', e.target.value)}
              />
            </Field>
            <Field label="ID contrat de maintenance">
              <TextInput
                value={form.maintenance_contract_id}
                onChange={(e) => set('maintenance_contract_id', e.target.value)}
              />
            </Field>
            <Field label="Niveau de criticité">
              <SelectInput
                value={form.criticality_level}
                onChange={(e) =>
                  set('criticality_level', e.target.value as FormState['criticality_level'])
                }
              >
                <option value="low">Faible</option>
                <option value="medium">Moyen</option>
                <option value="high">Critique</option>
              </SelectInput>
            </Field>
          </div>
        )}

        {currentStep === 'compliance' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Code GAUDÌ (registre Terna)">
              <TextInput
                value={form.gaudi_code}
                onChange={(e) => set('gaudi_code', e.target.value)}
              />
            </Field>
            <Field label="Certification CEI">
              <SelectInput
                value={form.cei_certification}
                onChange={(e) => set('cei_certification', e.target.value)}
              >
                <option value="CEI 0-16">CEI 0-16</option>
                <option value="CEI 0-21">CEI 0-21</option>
                <option value="N/A">N/A</option>
              </SelectInput>
            </Field>
            <Field label="Dernier test de conformité">
              <DateInput
                value={form.last_compliance_test_date}
                onChange={(e) => set('last_compliance_test_date', e.target.value)}
              />
            </Field>
            <Field label="URL audit log (rétention 5 ans Terna)">
              <TextInput
                value={form.data_retention_audit_url}
                onChange={(e) => set('data_retention_audit_url', e.target.value)}
                placeholder="s3://…"
              />
            </Field>
          </div>
        )}
      </section>

      {/* Footer nav */}
      <footer className="flex items-center justify-between">
        <button
          onClick={() =>
            setCurrentStep(STEPS[Math.max(0, currentIndex - 1)].id)
          }
          disabled={currentIndex === 0}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-700/50 text-slate-200 hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed text-sm"
        >
          <ChevronLeft className="w-4 h-4" />
          Précédent
        </button>
        <span className="text-xs text-slate-500">
          Étape {currentIndex + 1} / {STEPS.length}
        </span>
        {currentIndex < STEPS.length - 1 ? (
          <button
            onClick={() => setCurrentStep(STEPS[currentIndex + 1].id)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-700/50 text-slate-200 hover:bg-slate-700 text-sm"
          >
            Suivant
            <ChevronRight className="w-4 h-4" />
          </button>
        ) : (
          <button
            onClick={() => void handleSubmit()}
            disabled={!canSubmit || submitting}
            className="flex items-center gap-2 px-5 py-2 rounded-lg bg-success text-white font-medium hover:bg-success/90 disabled:opacity-50 disabled:cursor-not-allowed text-sm"
          >
            {submitting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Check className="w-4 h-4" />
            )}
            Créer la batterie
          </button>
        )}
      </footer>
    </div>
  );
}
