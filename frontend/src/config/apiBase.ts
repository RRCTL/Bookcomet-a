/**
 * Resolved API origin for fetch/axios.
 *
 * In `vite dev`, a shell or system `VITE_API_URL` can override `.env` and point at
 * `https://bookcometapi...` (502) or `http://localhost:8000` (mixed-content failure on
 * an HTTPS tunnel UI). When the page is served over HTTPS during dev, always use the
 * same-origin Vite proxy (see vite.config.ts).
 *
 * Built (`vite build`) apps: if `VITE_API_URL` is unset, HTTPS or a non-loopback hostname
 * uses same-origin `/api/*` so public hosts never default to the viewer's localhost:8000.
 */
function resolveApiBaseUrl(): string {
  const raw = (import.meta.env.VITE_API_URL as string | undefined)?.trim() ?? '';

  if (
    import.meta.env.DEV &&
    typeof window !== 'undefined' &&
    window.location.protocol === 'https:'
  ) {
    return '/api-proxy';
  }

  if (raw) return raw;
  if (import.meta.env.DEV) return '/api-proxy';

  const loc = typeof window !== 'undefined' ? window.location : null;
  if (loc && import.meta.env.PROD) {
    const h = loc.hostname;
    const loopback =
      h === '' || h === 'localhost' || h === '127.0.0.1' || h === '[::1]';
    const useSameOrigin = loc.protocol === 'https:' || !loopback;
    if (useSameOrigin) return '';
  }
  return 'http://127.0.0.1:8000';
}
export const API_BASE_URL = resolveApiBaseUrl();
