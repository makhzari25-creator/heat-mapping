const getBackend = () => process.env.BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || 'https://visionretail-iq.onrender.com';

export async function POST(request) {
  const BACKEND = getBackend();
  try {
    const body = await request.json();
    const res = await fetch(`${BACKEND}/events/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store',
      signal: AbortSignal.timeout(15000),
    });
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch (err) {
    console.error('ingest proxy error:', err.message);
    return Response.json({ error: 'backend_unavailable', detail: err.message }, { status: 503 });
  }
}