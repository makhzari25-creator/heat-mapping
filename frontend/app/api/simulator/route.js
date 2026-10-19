const getBackend = () => process.env.BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || 'https://visionretail-iq.onrender.com';

export async function GET(request) {
  const BACKEND = getBackend();
  try {
    const { searchParams } = new URL(request.url);
    const url = new URL(`${BACKEND}/simulator/status`);
    searchParams.forEach((v, k) => url.searchParams.set(k, v));
    const res = await fetch(url.toString(), {
      cache: 'no-store',
      signal: AbortSignal.timeout(15000),
    });
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch (err) {
    console.error('Simulator status proxy error:', err.message);
    return Response.json({ error: 'backend_unavailable', detail: err.message }, { status: 503 });
  }
}

export async function POST(request) {
  const BACKEND = getBackend();
  try {
    const body = await request.json();
    const res = await fetch(`${BACKEND}/simulator/control`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch (err) {
    console.error('Simulator control proxy error:', err.message);
    return Response.json({ error: 'backend_unavailable', detail: err.message }, { status: 503 });
  }
}
