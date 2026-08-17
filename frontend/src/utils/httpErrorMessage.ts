import axios from 'axios';

/** FastAPI validation errors use `detail` as an array of `{ loc, msg, type }`. */
function formatFastApiDetail(detail: unknown): string | null {
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (item && typeof item === 'object' && 'msg' in item && typeof (item as { msg: unknown }).msg === 'string') {
        return (item as { msg: string }).msg;
      }
      return JSON.stringify(item);
    });
    const joined = parts.filter(Boolean).join('; ');
    return joined || null;
  }
  return null;
}

/**
 * Human-readable message for login / auth flows: prefers FastAPI `detail`,
 * then Axios "Network Error" when there is no response, then a safe fallback.
 */
export function getLoginFlowErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = formatFastApiDetail(err.response?.data && (err.response.data as { detail?: unknown }).detail);
    if (detail) return detail;
    const msgLower = typeof err.message === 'string' ? err.message.toLowerCase() : '';
    if ((!err.response && msgLower.includes('timeout')) || err.code === 'ECONNABORTED') {
      return 'The API did not respond in time. Check that the backend is running, VITE/dev proxy reaches it, then try again.';
    }
    if (!err.response && err.message) return err.message;
    if (err.response) {
      const st = err.response.status;
      const text = err.response.statusText;
      if (st && text) return `${st} ${text}`;
    }
  }
  if (err instanceof Error && err.message) return err.message;
  return 'Sign in failed. Please check your credentials.';
}
