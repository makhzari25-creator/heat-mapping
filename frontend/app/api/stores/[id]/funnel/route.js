const getBackend = () => process.env.BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || 'https://visionretail-iq.onrender.com';

export async function GET(request, { params }) {
  const BACKEND = getBackend();
  try {
    const { searchParams } = new URL(request.url);
    const res = await fetch(
      `${BACKEND}/stores/${params.id}/funnel?${searchParams}`,
      { cache: 'no-store', signal: AbortSignal.timeout(15000) }
    );
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch (err) {
    console.error('funnel proxy error:', err.message);
    return Response.json({ error: 'backend_unavailable', detail: err.message }, { status: 503 });
  }
}