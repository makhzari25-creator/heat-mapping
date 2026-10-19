'use client';
import { useState, useEffect, useCallback } from 'react';

export function useStoreConfig() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch('/api/config', { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setConfig(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const stores = config?.stores || [];
  const primaryStore = stores[0] || null;

  const getZoneLabel = useCallback(
    (zoneId) => {
      if (!primaryStore) return zoneId;
      const zone = primaryStore.zones?.find((z) => z.zone_id === zoneId);
      return zone?.name || zoneId;
    },
    [primaryStore]
  );

  return {
    config,
    stores,
    primaryStore,
    loading,
    error,
    getZoneLabel,
    refetch: fetchConfig,
  };
}
