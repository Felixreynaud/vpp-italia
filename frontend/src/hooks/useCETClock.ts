import { useState, useEffect } from 'react';

export function useCETClock(): string {
  const [time, setTime] = useState(() => getCETTime());

  useEffect(() => {
    const interval = setInterval(() => {
      setTime(getCETTime());
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  return time;
}

function getCETTime(): string {
  return new Date().toLocaleTimeString('it-IT', {
    timeZone: 'Europe/Rome',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}
