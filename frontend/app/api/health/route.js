const getBackend = () => process.env.BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || 'https://visionretail-iq.onrender.com';

export async function GET() {
  const BACKEND = getBackend();
  try {
    const res = await fetch(`${BACKEND}/health`, 
      { cache: 'no-store', signal: AbortSignal.timeout(15000) }
    );
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch (err) {
    console.error('health proxy error:', err.message);
    return Response.json({ error: 'backend_unavailable', status: 'offline', detail: err.message }, { status: 503 });
  }
}