import { useMemo } from 'react';
import type { FleetMetrics } from '../api/types';

interface EnergyFlowProps {
  metrics: FleetMetrics | null;
}

export function EnergyFlow({ metrics }: EnergyFlowProps) {
  const power = metrics?.puissance_totale_kw ?? 0;
  const soc = metrics?.soc_moyen ?? 0;
  // Convention batterie : positive = charge (la batterie reçoit),
  // négative = décharge (la batterie injecte au réseau).
  const isCharging = power > 0;
  const isDischarging = power < 0;
  const absPower = Math.abs(power);

  const maxPower = 5000;
  const duration = useMemo(() => {
    if (absPower < 10) return 3;
    return Math.max(0.5, 3 - (absPower / maxPower) * 2.5);
  }, [absPower]);

  const arrowColor = isCharging ? '#3b82f6' : isDischarging ? '#22c55e' : '#64748b';
  const arrowLabel = isCharging
    ? `Charge ${absPower.toFixed(0)} kW`
    : isDischarging
    ? `Decharge ${absPower.toFixed(0)} kW`
    : 'En attente';

  const socFillPct = Math.min(100, Math.max(0, soc));
  const socFillY = 130 - (100 * socFillPct) / 100;

  const socColor =
    soc < 10 ? '#ef4444' :
    soc < 20 ? '#f59e0b' :
    soc > 92 ? '#ef4444' :
    soc > 85 ? '#f59e0b' :
    '#22c55e';

  return (
    <div className="flex flex-col items-center" role="img" aria-label={`Flux d'energie: ${arrowLabel}`}>
      <svg viewBox="0 0 420 180" className="w-full max-w-lg" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <g transform="translate(40, 40)">
          <rect x="5" y="10" width="60" height="100" rx="6" ry="6" fill="#1e293b" stroke="#334155" strokeWidth="2" />
          <rect x="20" y="4" width="30" height="8" rx="3" ry="3" fill="#334155" />
          <clipPath id="batClip">
            <rect x="7" y="12" width="56" height="96" rx="4" />
          </clipPath>
          <rect
            x="7"
            y={socFillY - 10}
            width="56"
            height={110 - (socFillY - 10) + 10}
            fill={socColor}
            fillOpacity="0.7"
            clipPath="url(#batClip)"
            style={{ transition: 'y 0.5s ease, height 0.5s ease, fill 0.5s ease' }}
          />
          <text x="35" y="68" textAnchor="middle" fontSize="14" fontWeight="bold" fill="white">
            {soc.toFixed(0)}%
          </text>
          <text x="35" y="84" textAnchor="middle" fontSize="8" fill="#94a3b8">SoC</text>
          {isCharging && (
            <text x="35" y="48" textAnchor="middle" fontSize="16" fill="#3b82f6">⚡</text>
          )}
        </g>
        <g>
          <line x1="120" y1="90" x2="300" y2="90" stroke="#334155" strokeWidth="2" />
          {(isDischarging || isCharging) && (
            <line
              x1="120" y1="90" x2="300" y2="90"
              stroke={arrowColor}
              strokeWidth="4"
              strokeLinecap="round"
              className={isDischarging ? 'flow-discharge' : 'flow-charge'}
              style={{ '--flow-duration': `${duration}s` } as React.CSSProperties}
            />
          )}
          {isDischarging && <polygon points="295,83 310,90 295,97" fill={arrowColor} />}
          {isCharging && <polygon points="125,83 110,90 125,97" fill={arrowColor} />}
          <rect x="168" y="74" width="84" height="22" rx="11" fill="#0f172a" stroke="#334155" />
          <text x="210" y="89" textAnchor="middle" fontSize="11" fontWeight="bold" fill={arrowColor}>
            {absPower > 0 ? `${absPower >= 1000 ? (absPower / 1000).toFixed(1) + ' MW' : absPower.toFixed(0) + ' kW'}` : '—'}
          </text>
        </g>
        <g transform="translate(310, 40)">
          <rect x="28" y="80" width="14" height="30" rx="2" fill="#334155" />
          <rect x="10" y="55" width="50" height="6" rx="2" fill="#334155" />
          <rect x="18" y="35" width="34" height="5" rx="2" fill="#334155" />
          <line x1="35" y1="10" x2="10" y2="55" stroke="#334155" strokeWidth="2.5" />
          <line x1="35" y1="10" x2="60" y2="55" stroke="#334155" strokeWidth="2.5" />
          <line x1="35" y1="10" x2="20" y2="35" stroke="#334155" strokeWidth="2" />
          <line x1="35" y1="10" x2="50" y2="35" stroke="#334155" strokeWidth="2" />
          <text x="25" y="130" textAnchor="middle" fontSize="20" fill="#f59e0b">⚡</text>
          <text x="35" y="148" textAnchor="middle" fontSize="9" fill="#94a3b8">RESEAU</text>
        </g>
        <text x="70" y="165" textAnchor="middle" fontSize="10" fill="#94a3b8">BATTERIES</text>
        <text x="70" y="178" textAnchor="middle" fontSize="9" fill="#64748b">
          {metrics?.batteries_actives ?? 0}/{metrics?.batteries_total ?? 0} actives
        </text>
        <text x="210" y="130" textAnchor="middle" fontSize="10" fill={arrowColor} fontWeight="500">
          {arrowLabel}
        </text>
      </svg>
    </div>
  );
}
