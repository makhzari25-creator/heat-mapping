'use client';
import { useState, useEffect, useRef } from 'react';

export function useMetrics(storeId, intervalMs = 5000, dateStr = '') {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const intervalRef = useRef(null);

  const fetchMetrics = async () => {
    try {
      const url = `/api/stores/${storeId}/metrics` + (dateStr ? `?date_str=${dateStr}` : '');
      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setLastUpdated(new Date());
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!storeId) return;
    setLoading(true);
    fetchMetrics();
    intervalRef.current = setInterval(fetchMetrics, intervalMs);
    return () => clearInterval(intervalRef.current);
  }, [storeId, intervalMs, dateStr]);

  return { data, loading, error, lastUpdated, refetch: fetchMetrics };
}
