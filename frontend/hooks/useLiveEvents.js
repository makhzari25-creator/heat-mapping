'use client';
import { useState, useEffect, useRef, useCallback } from 'react';

export function useLiveEvents(storeId, maxEvents = 30) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const seenIds = useRef(new Set());
  const lastTimestamp = useRef(null);

  const fetchLatest = useCallback(async () => {
    if (!storeId) return;
    setLoading(true);
    try {
      const url = new URL('/api/events', window.location.origin);
      url.searchParams.set('storeId', storeId);
      url.searchParams.set('limit', String(maxEvents));
      if (lastTimestamp.current) {
        url.searchParams.set('since', lastTimestamp.current);
      }
      const res = await fetch(url.toString(), { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const newEvents = [];
      for (const ev of data.events || []) {
        if (!seenIds.current.has(ev.event_id)) {
          seenIds.current.add(ev.event_id);
          newEvents.push(ev);
        }
        if (!lastTimestamp.current || ev.timestamp > lastTimestamp.current) {
          lastTimestamp.current = ev.timestamp;
        }
      }
      if (newEvents.length > 0) {
        setEvents(prev => [...newEvents, ...prev].slice(0, maxEvents));
      }
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [storeId, maxEvents]);

  useEffect(() => {
    if (!storeId) return;
    setEvents([]);
    seenIds.current = new Set();
    lastTimestamp.current = null;
    fetchLatest();
    const interval = setInterval(fetchLatest, 4000);
    return () => clearInterval(interval);
  }, [storeId, fetchLatest]);

  return { events, loading, error, refetch: fetchLatest };
}
