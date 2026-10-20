'use client';
import { useEffect, useMemo, useState } from 'react';

// Two-floor hit map: zones come from store_layout.json (plan_polygon, floor),
// heat comes from /stores/{id}/heatmap?floor=N. Polygon coords are 0-1 on the
// floor plan; an optional floor_plans[floor].image is drawn underneath.

const W = 1000;
const H = 640;

// 0..100 -> blue -> amber -> red
function heatColor(v) {
  const t = Math.max(0, Math.min(1, v / 100));
  const stops = [[59, 130, 246], [245, 158, 11], [239, 68, 68]];
  const k = t * 2;
  const i = Math.min(1, Math.floor(k));
  const f = k - i;
  const c = stops[i].map((a, j) => Math.round(a + (stops[i + 1][j] - a) * f));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

function polyPoints(poly) {
  return poly.map(([x, y]) => `${x * W},${y * H}`).join(' ');
}

function centroid(poly) {
  const n = poly.length;
  return [poly.reduce((s, p) => s + p[0], 0) / n, poly.reduce((s, p) => s + p[1], 0) / n];
}

export default function HitMapPage() {
  const [config, setConfig] = useState(null);
  const [storeId, setStoreId] = useState(null);
  const [floor, setFloor] = useState(null);
  const [date, setDate] = useState('');
  const [heat, setHeat] = useState(null);
  const [error, setError] = useState(null);
  const [hover, setHover] = useState(null);

  useEffect(() => {
    fetch('/api/config', { cache: 'no-store' })
      .then((r) => r.json())
      .then((c) => {
        setConfig(c);
        if (c.stores?.[0]) setStoreId(c.stores[0].store_id);
      })
      .catch((e) => setError(e.message));
  }, []);

  const store = useMemo(() => config?.stores?.find((s) => s.store_id === storeId), [config, storeId]);
  const floors = useMemo(() => {
    const f = [...new Set((store?.zones || []).map((z) => z.floor).filter((x) => x != null).map(String))];
    return f.sort();
  }, [store]);

  useEffect(() => {
    if (floors.length && !floors.includes(floor)) setFloor(floors[0]);
  }, [floors, floor]);

  useEffect(() => {
    if (!storeId) return;
    let alive = true;
    const load = () => {
      const q = new URLSearchParams();
      if (floor) q.set('floor', floor);
      if (date) q.set('date_str', date);
      fetch(`/api/stores/${storeId}/heatmap?${q}`, { cache: 'no-store' })
        .then((r) => r.json())
        .then((d) => { if (alive) { setHeat(d); setError(null); } })
        .catch((e) => alive && setError(e.message));
    };
    load();
    const t = setInterval(load, 10000);
    return () => { alive = false; clearInterval(t); };
  }, [storeId, floor, date]);

  const stats = useMemo(() => {
    const m = {};
    (heat?.zones || []).forEach((z) => { m[z.zone_id] = z; });
    return m;
  }, [heat]);

  const zones = (store?.zones || []).filter((z) => floors.length === 0 || String(z.floor) === floor);
  const ranked = [...(heat?.zones || [])].sort((a, b) => b.visit_frequency - a.visit_frequency);
  const plan = store?.floor_plans?.[floor];

  const card = { background: 'rgba(17,19,31,0.65)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 12, padding: 16 };
  const tab = (active) => ({
    padding: '8px 18px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.12)', cursor: 'pointer',
    background: active ? '#6366f1' : 'transparent', color: '#f3f4f6', fontSize: 14,
  });

  return (
    <main style={{ padding: 24, maxWidth: 1280, margin: '0 auto' }}>
      <header style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, marginRight: 'auto' }}>Customer hit map</h1>
        {config?.stores?.length > 1 && (
          <select value={storeId || ''} onChange={(e) => setStoreId(e.target.value)}
            style={{ background: '#11131f', color: '#f3f4f6', padding: 8, borderRadius: 8 }}>
            {config.stores.map((s) => <option key={s.store_id} value={s.store_id}>{s.name}</option>)}
          </select>
        )}
        {floors.map((f) => (
          <button key={f} style={tab(f === floor)} onClick={() => setFloor(f)}>
            {store?.floor_plans?.[f]?.label || `Floor ${f}`}
          </button>
        ))}
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
          style={{ background: '#11131f', color: '#f3f4f6', padding: 8, borderRadius: 8, border: '1px solid rgba(255,255,255,0.12)' }} />
      </header>

      {error && <p style={{ color: '#ef4444', marginBottom: 12 }}>Backend unavailable: {error}</p>}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2.2fr) minmax(260px, 1fr)', gap: 16 }}>
        <section style={card}>
          <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block', borderRadius: 8, background: '#0d0f18' }}>
            {plan?.image && <image href={plan.image} x="0" y="0" width={W} height={H} preserveAspectRatio="none" opacity="0.55" />}
            {zones.map((z) => {
              const poly = z.plan_polygon || z.polygon;
              const s = stats[z.zone_id];
              const v = s?.visit_frequency ?? 0;
              const [cx, cy] = centroid(poly);
              return (
                <g key={z.zone_id} onMouseEnter={() => setHover(z.zone_id)} onMouseLeave={() => setHover(null)}>
                  <polygon points={polyPoints(poly)} fill={s?.visits ? heatColor(v) : '#1f2335'}
                    fillOpacity={s?.visits ? 0.35 + 0.5 * (v / 100) : 0.5}
                    stroke={hover === z.zone_id ? '#fff' : 'rgba(255,255,255,0.35)'} strokeWidth={hover === z.zone_id ? 3 : 1.5} />
                  <text x={cx * W} y={cy * H - 6} textAnchor="middle" fill="#f3f4f6" fontSize="20" fontWeight="600">{z.name}</text>
                  <text x={cx * W} y={cy * H + 20} textAnchor="middle" fill="#d1d5db" fontSize="17">
                    {s ? `${s.visits} visits · ${s.avg_dwell_minutes} min` : '—'}
                  </text>
                </g>
              );
            })}
          </svg>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12, fontSize: 12, color: '#9ca3af' }}>
            <span>Less traffic</span>
            <div style={{ flex: 1, maxWidth: 240, height: 8, borderRadius: 4, background: `linear-gradient(90deg, ${heatColor(0)}, ${heatColor(50)}, ${heatColor(100)})` }} />
            <span>More traffic</span>
            {heat?.data_confidence && <span style={{ marginLeft: 'auto' }}>
              confidence: {heat.data_confidence.confidence_level} (n={heat.data_confidence.supporting_sample_size})
            </span>}
          </div>
        </section>

        <aside style={card}>
          <h2 style={{ fontSize: 15, marginBottom: 10, fontWeight: 600 }}>Busiest areas</h2>
          {ranked.length === 0 && <p style={{ color: '#9ca3af', fontSize: 14 }}>No data yet for this floor.</p>}
          <ol style={{ listStyle: 'none', display: 'grid', gap: 8 }}>
            {ranked.map((z, i) => (
              <li key={z.zone_id} onMouseEnter={() => setHover(z.zone_id)} onMouseLeave={() => setHover(null)}
                style={{ display: 'grid', gridTemplateColumns: '22px 1fr auto', gap: 8, alignItems: 'center', fontSize: 14,
                  opacity: hover && hover !== z.zone_id ? 0.5 : 1 }}>
                <span style={{ color: '#6b7280' }}>{i + 1}</span>
                <span>
                  {z.zone_name}
                  <div style={{ height: 4, borderRadius: 2, marginTop: 4, width: `${Math.max(4, z.visit_frequency)}%`, background: heatColor(z.visit_frequency) }} />
                </span>
                <span style={{ color: '#9ca3af', fontSize: 12, textAlign: 'right' }}>
                  {z.visits} visits<br />{z.avg_dwell_minutes} min
                </span>
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </main>
  );
}
