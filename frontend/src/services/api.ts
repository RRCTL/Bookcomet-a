import { API_BASE_URL } from '../config/apiBase';

export { API_BASE_URL };
export const apiUrl = (path: string): string =>
  `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;

/**
 * Only send cross-site cookies when the API origin differs from the page (e.g. SPA on Vercel + API on api.*).
 * Same-origin / relative VITE_API_URL avoids credentialed CORS preflight issues that can blank the app.
 */
function _apiFetchCredentials(): RequestCredentials {
  if (typeof window === 'undefined') return 'same-origin';
  try {
    const base = (API_BASE_URL || '').trim();
    if (!base || base.startsWith('/')) return 'same-origin';
    const apiOrigin = new URL(base, window.location.href).origin;
    return apiOrigin === window.location.origin ? 'same-origin' : 'include';
  } catch {
    return 'same-origin';
  }
}

/** GET /health — liveness plus safe feature flags (`app.core.config`). */
export type HealthCheckResponse = {
  status: string
  feature_process_intake_v2?: boolean
  /** True when `AP_CROSS_VLM_MODEL` is set — enables AP cross-VLM double-check UI + endpoint. */
  ap_cross_vlm_configured?: boolean
  /** Server-side auto merge of primary + cross VLM for AP (see `AP_AUTO_CROSS_VERIFY_ENABLED`). */
  ap_auto_cross_verify_enabled?: boolean
  /** Cross model configured and auto merge enabled. */
  ap_cross_verify_pipeline_active?: boolean
  /** True when backend REGISTER_INVITE_CODE is set — show invite field on register. */
  register_invite_required?: boolean
}

export type ApCrossVerifyRequest = {
  file_ids: string[]
  multi_receipt_confirmed?: boolean
  multi_receipt_acknowledged?: boolean
  force_process?: boolean
}

export type ApCrossVerifyResponse = {
  model: string
  results: Array<{ file_id: string; filename: string; result: Record<string, unknown> }>
}

/** Row from GET /company/classification-rules */
export interface ClassificationRuleApiRow {
  id: string
  rule_name: string
  rule_type: string
  pattern_type: string
  pattern: string
  document_type: string | null
  notes: string | null
  use_when?: string | null
  content?: string | null
  is_active: boolean
  hit_count: number
  priority: number
  last_hit_at: string | null
  created_at: string | null
}

// Access token getter — injected by AuthContext at runtime so this module
// stays free of React imports.
let _getAccessToken: (() => string | null) | null = null;
export const setAccessTokenGetter = (fn: () => string | null) => {
  _getAccessToken = fn;
};

// Token refresh callback — injected by AuthContext so apiFetch can silently
// rotate the JWT when it receives a 401, mirroring the axios interceptor.
let _refreshToken: (() => Promise<string>) | null = null;
export const setTokenRefresher = (fn: () => Promise<string>) => {
  _refreshToken = fn;
};

/** After a failed /auth/refresh, skip retry spam (idle token-usage polling, many tabs). */
let _authRefreshCooldownUntil = 0;
const DEFAULT_AUTH_REFRESH_COOLDOWN_MS = 120_000;

export function clearAuthRefreshCooldown(): void {
  _authRefreshCooldownUntil = 0;
}

export function markAuthRefreshCooldown(ms: number = DEFAULT_AUTH_REFRESH_COOLDOWN_MS): void {
  _authRefreshCooldownUntil = Date.now() + ms;
}

function _isAuthRefreshCoolingDown(): boolean {
  return Date.now() < _authRefreshCooldownUntil;
}

/** Clear session when fetch-path refresh fails (align with axios interceptor). */
let _onApiRefreshFailure: (() => void) | null = null;
export function setApiRefreshFailureHandler(fn: (() => void) | null): void {
  _onApiRefreshFailure = fn;
}

// Company ID getter — resolved from the auth context after login.
let _getCompanyId: (() => string) | null = null;
export const setCompanyIdGetter = (fn: () => string) => {
  _getCompanyId = fn;
};

export const withScopeHeaders = (
  headers?: HeadersInit,
  token?: string | null,
  companyIdOverride?: string | null,
): Headers => {
  const merged = new Headers(headers);
  const tok = token !== undefined ? token : _getAccessToken?.();

  if (tok) {
    merged.set('Authorization', `Bearer ${tok}`);
    if (!merged.has('X-Company-ID')) {
      merged.set('X-Company-ID', companyIdOverride || _getCompanyId?.() || 'default');
    }
  }

  return merged;
};

/** fetch init with optional client-side timeout (covers hung LLM / network). */
export type ApiFetchInit = Omit<RequestInit, 'signal'> & {
  signal?: AbortSignal;
  /** If set, aborts the request after this many ms (each 401-retry gets a fresh budget). */
  timeoutMs?: number;
  /** Pin requests to the company where a background task/job was started. */
  companyId?: string | null;
};

function abortSignalWithTimeout(
  userSignal: AbortSignal | undefined,
  timeoutMs: number,
): AbortSignal | undefined {
  if (timeoutMs <= 0) return userSignal;
  const ctrl = new AbortController();
  const tid = setTimeout(() => {
    ctrl.abort(new DOMException('The operation was aborted due to timeout', 'AbortError'));
  }, timeoutMs);
  const clear = () => clearTimeout(tid);
  if (userSignal) {
    if (userSignal.aborted) {
      clear();
      ctrl.abort(userSignal.reason);
      return ctrl.signal;
    }
    userSignal.addEventListener(
      'abort',
      () => {
        clear();
        ctrl.abort(userSignal.reason);
      },
      { once: true },
    );
  }
  return ctrl.signal;
}

// apiFetch with automatic 401 → token-refresh → retry (one attempt).
// Mirrors the axios interceptor in AuthContext so long-running OCR sessions
// don't silently drop snapshot saves when the access token expires.
export const apiFetch = async (path: string, init: ApiFetchInit = {}): Promise<Response> => {
  const { timeoutMs, signal: userSignal, companyId, ...rest } = init;

  const runFetch = async (tokenOverride?: string | null): Promise<Response> => {
    const signal =
      timeoutMs != null && timeoutMs > 0
        ? abortSignalWithTimeout(userSignal, timeoutMs)
        : userSignal;
    try {
      return await fetch(apiUrl(path), {
        ...rest,
        credentials: _apiFetchCredentials(),
        ...(signal ? { signal } : {}),
        headers: withScopeHeaders(rest.headers, tokenOverride, companyId),
      });
    } catch (err) {
      const detail =
        err instanceof DOMException && err.name === 'AbortError'
          ? 'Request timed out'
          : err instanceof Error
            ? err.message
            : 'Network error';
      return new Response(JSON.stringify({ detail }), {
        status: 503,
        statusText: 'Network Error',
        headers: { 'Content-Type': 'application/json' },
      });
    }
  };

  const res = await runFetch();

  if (res.status === 401 && _refreshToken) {
    if (_isAuthRefreshCoolingDown()) {
      return res;
    }
    try {
      const newToken = await _refreshToken();
      clearAuthRefreshCooldown();
      return await runFetch(newToken);
    } catch {
      markAuthRefreshCooldown();
      try {
        _onApiRefreshFailure?.();
      } catch {
        /* ignore */
      }
      return res;
    }
  }

  return res;
};

interface OcrLine {
  text: string;
  confidence: number;
  bbox: number[];
}

interface OcrResult {
  text?: string;
  lines?: OcrLine[];
  provider?: string;
  needs_confirmation?: boolean;
  message?: string;
  document_type?: string;
  total_pages?: number;
  pages?: unknown[];
  ocr_job_outcome?: 'ok' | 'partial' | 'failed';
  raw_ocr?: unknown;
  [key: string]: unknown;
}

export type { OcrResult, OcrLine };

/** localStorage key prefix — survive refresh to resume polling background work */
export const BG_JOB_STORAGE_PREFIX = 'bookcomet_bg_job_';

export type BackgroundJobRecord = {
  id: string;
  job_type: string;
  status: string;
  result_json?: Record<string, unknown> | null;
  error_text?: string | null;
  task_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  progress_percent?: number | null;
  progress_label?: string | null;
  original_filename?: string | null;
  storage_retained_until?: string | null;
  ocr_retry_eligible?: boolean;
};

export type BankUploadActiveRecord = {
  job_id: string;
  task_id: string;
  company_id: string;
  owner_user_id?: string | null;
  filename: string;
  status: string;
  progress_percent: number;
  label: string;
  page_current: number;
  page_total: number;
  page_verification: Record<string, string>;
};

export type WorkspaceActivityRecord = {
  bank_uploads: BankUploadActiveRecord[];
  background_jobs: BackgroundJobRecord[];
};

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export const api = {
  async createOcrBackgroundJob(
    file: File,
    processingMode?: string,
    multiReceiptConfirmed?: boolean,
    taskId?: string,
    companyId?: string | null,
    opts?: {
      apVlmReceiptSignal?: 'guess' | 'single_per_page' | 'multi_per_page' | 'single_span_pages'
      apVlmTablePreset?: 'default' | 'ap_table'
    },
  ): Promise<{ job_id: string; status: string }> {
    const formData = new FormData();
    formData.append('file', file);
    if (processingMode) {
      formData.append('processing_mode', processingMode);
    }
    if (multiReceiptConfirmed) {
      formData.append('multi_receipt_confirmed', 'true');
    }
    if (taskId) {
      formData.append('task_id', taskId);
    }
    const rs = opts?.apVlmReceiptSignal?.trim()
    const tp = opts?.apVlmTablePreset?.trim()
    if (rs) formData.append('ap_vlm_receipt_signal', rs)
    if (tp && tp !== 'default') formData.append('ap_vlm_table_preset', tp)

    const response = await apiFetch('/api/jobs/ocr', {
      method: 'POST',
      body: formData,
      companyId,
    });

    if (response.status === 429) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(typeof error.detail === 'string' ? error.detail : response.statusText);
    }
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(`OCR job start failed: ${error.detail || response.statusText}`);
    }

    return response.json();
  },

  async createAiChatBackgroundJob(req: {
    session_id: string;
    mode: string;
    message: string;
    context: {
      transactions: Record<string, unknown>[];
      coa?: Record<string, unknown>[];
      recon?: Record<string, unknown>;
      report?: Record<string, unknown>;
    };
  }, companyId?: string | null): Promise<{ job_id: string; status: string }> {
    const response = await apiFetch('/api/jobs/ai-chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
      companyId,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(`AI chat job start failed: ${error.detail || response.statusText}`);
    }
    return response.json();
  },

  async createCoaDeployBackgroundJob(
    body: {
      mode: string;
      batches: Array<{
        task_id: string;
        batch_id: string;
        run_id: string;
        transactions: Record<string, unknown>[];
        base_payload?: Record<string, unknown>;
        table_preset?: string;
      }>;
    },
    companyId?: string | null,
  ): Promise<{ job_id: string; status: string }> {
    const response = await apiFetch('/api/jobs/coa-deploy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      companyId,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(`CoA deploy job start failed: ${error.detail || response.statusText}`);
    }
    return response.json();
  },

  async getBackgroundJob(jobId: string, companyId?: string | null): Promise<BackgroundJobRecord> {
    const response = await apiFetch(`/api/jobs/${encodeURIComponent(jobId)}`, { companyId });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || response.statusText);
    }
    return response.json();
  },

  async retryOcrJobPage(jobId: string, page: number, companyId?: string | null): Promise<BackgroundJobRecord> {
    const response = await apiFetch(
      `/api/jobs/${encodeURIComponent(jobId)}/ocr-retry-page?page=${encodeURIComponent(String(page))}`,
      { method: 'POST', companyId },
    );
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || response.statusText);
    }
    return response.json();
  },

  async cancelBackgroundJob(jobId: string): Promise<BackgroundJobRecord> {
    const response = await apiFetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: 'POST',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || response.statusText);
    }
    return response.json();
  },

  async listActiveBackgroundJobs(): Promise<BackgroundJobRecord[]> {
    const response = await apiFetch('/api/jobs');
    if (!response.ok) {
      return [];
    }
    return response.json();
  },

  async getWorkspaceActivity(): Promise<WorkspaceActivityRecord> {
    const response = await apiFetch('/api/workspace/activity', { timeoutMs: 45_000 });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || response.statusText);
    }
    return response.json();
  },

  async waitForBackgroundJob<T = Record<string, unknown>>(
    jobId: string,
    opts?: {
      pollMs?: number
      isCancelled?: () => boolean
      companyId?: string | null
      onProgress?: (status: BackgroundJobRecord) => void
    },
  ): Promise<T> {
    const pollMs = opts?.pollMs ?? 1200;
    for (;;) {
      if (opts?.isCancelled?.()) {
        throw new DOMException('Resume polling cancelled', 'AbortError');
      }
      const st = await this.getBackgroundJob(jobId, opts?.companyId);
      opts?.onProgress?.(st);
      if (st.status === 'completed' && st.result_json != null) {
        return st.result_json as T;
      }
      if (st.status === 'failed') {
        throw new Error(st.error_text || 'Background job failed');
      }
      if (st.status === 'cancelled') {
        throw new DOMException(st.error_text || 'Background job cancelled', 'AbortError');
      }
      await sleep(pollMs);
    }
  },

  async uploadForOcr(
    file: File,
    processingMode?: string,
    multiReceiptConfirmed?: boolean,
    taskId?: string,
  ): Promise<OcrResult> {
    const { job_id } = await this.createOcrBackgroundJob(
      file,
      processingMode,
      multiReceiptConfirmed,
      taskId,
    );
    return this.waitForBackgroundJob(job_id) as Promise<OcrResult>;
  },

  async healthCheck(): Promise<HealthCheckResponse> {
    const response = await apiFetch('/health');
    if (!response.ok) {
      throw new Error('Health check failed');
    }
    return response.json();
  },

  async uploadBankStatement(file: File): Promise<any> {
    const formData = new FormData();
    formData.append('file', file);
    
    const response = await apiFetch('/bank-statements/upload', {
      method: 'POST',
      body: formData,
    });
    
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(`Bank statement upload failed: ${error.detail || response.statusText}`);
    }
    
    return response.json();
  },

  async startBankStatementUploadJob(
    file: File,
    taskId: string,
    companyId?: string | null,
  ): Promise<{ job_id: string; status: string }> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('task_id', taskId);

    const response = await apiFetch('/bank-statements/upload/start', {
      method: 'POST',
      body: formData,
      companyId,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(`Bank statement upload start failed: ${error.detail || response.statusText}`);
    }

    return response.json();
  },

  async getBankStatementUploadJobStatus(jobId: string, companyId?: string | null): Promise<any> {
    const response = await apiFetch(`/bank-statements/upload/status/${encodeURIComponent(jobId)}`, { companyId });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(`Bank statement upload status failed: ${error.detail || response.statusText}`);
    }
    return response.json();
  },

  async cancelBankStatementUploadJob(jobId: string, companyId?: string | null): Promise<any> {
    const response = await apiFetch(`/bank-statements/upload/cancel/${encodeURIComponent(jobId)}`, {
      method: 'POST',
      companyId,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(`Bank statement upload cancel failed: ${error.detail || response.statusText}`);
    }
    return response.json();
  },

  async getFilePageCount(file: File): Promise<number> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await apiFetch('/bank-statements/page-count', {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      return 1;
    }

    const data = await response.json().catch(() => ({ page_count: 1 }));
    const count = Number(data?.page_count);
    if (!Number.isFinite(count) || count <= 0) {
      return 1;
    }
    return Math.max(1, Math.floor(count));
  },

  async aiChat(req: {
    session_id: string;
    mode: string;
    message: string;
    context: {
      transactions: Record<string, unknown>[];
      coa?: Record<string, unknown>[];
      /** RECON workspace context + allow-lists for validated match/unmatch proposals */
      recon?: Record<string, unknown>;
    };
  }, companyId?: string | null): Promise<{
    reply: string;
    table_patches: { id_number: string; field: string; value: unknown }[];
    save_rule_pending?: boolean;
    save_rule_proposal?: { type: string; vendor?: string; keywords?: string[]; field: string; value: string } | null;
    rule_saved?: boolean;
    rule_saved_message?: string;
    recon_actions?: {
      op: string;
      bank_txn_ids?: string[];
      ledger_txn_ids?: string[];
      group_id?: string | null;
      journal_id?: string | null;
      voucher_no?: string | null;
      gl_lines?: Array<{
        line_id?: string | null;
        account_code?: string | null;
        memo?: string | null;
        debit?: number | null;
        credit?: number | null;
      }>;
      deleted_line_ids?: string[];
    }[];
    redirect_tasks?: {
      task_id: string;
      title?: string;
      mode?: string;
      reason?: string;
      fields?: string[];
    }[];
    recon_redirect?: {
      gl_display?: string | null;
      reason_zh: string;
      reason_en: string;
    } | null;
  }> {
    const { job_id } = await this.createAiChatBackgroundJob(req, companyId);
    return this.waitForBackgroundJob(job_id, { companyId });
  },

  async generateTitle(
    messages: { role: string; content: string }[],
    mode: string,
  ): Promise<string> {
    try {
      const response = await apiFetch('/api/ai-title', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages, mode }),
      });
      if (!response.ok) return `${mode} chat`;
      const data = await response.json().catch(() => ({}));
      return data.title || `${mode} chat`;
    } catch {
      return `${mode} chat`;
    }
  },

  async getTokenUsage(period: 'month' | 'week' | 'session' = 'month', taskId?: string): Promise<{
    period: string;
    total_calls: number;
    total_tokens: number;
    prompt_tokens: number;
    completion_tokens: number;
    estimated_cost_usd: number;
    breakdown: { call_type: string; calls: number; total_tokens: number; estimated_cost_usd: number }[];
  }> {
    const params = new URLSearchParams({ period });
    if (taskId) params.set('task_id', taskId);
    const res = await apiFetch(`/api/token-usage?${params}`);
    if (!res.ok) return { period, total_calls: 0, total_tokens: 0, prompt_tokens: 0, completion_tokens: 0, estimated_cost_usd: 0, breakdown: [] };
    return res.json();
  },

  /** POST /companies — create workspace (JWT); ignores active X-Company-ID for auth. */
  async createCompany(name: string): Promise<{
    id: string;
    name: string;
    role: string;
  }> {
    const res = await apiFetch('/companies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim() }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err as { detail?: string }).detail || res.statusText);
    }
    return res.json();
  },

  async deleteCompany(
    companyId: string,
    confirmName: string,
  ): Promise<{ deleted_id: string; suggested_company_id: string | null }> {
    const res = await apiFetch(`/companies/${encodeURIComponent(companyId)}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm_name: confirmName }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err as { detail?: string }).detail || res.statusText);
    }
    return res.json() as Promise<{ deleted_id: string; suggested_company_id: string | null }>;
  },

  async routeToOther(payload: {
    source_task_id: string;
    source_file_id?: string;
    document_subtype: 'loan' | 'fixed_asset';
    ocr_text: string;
    gate_document_hint?: string;
  }): Promise<{ task_id: string; record_id: string; task_title: string }> {
    const res = await apiFetch('/api/other/route', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`Route to Other failed: ${res.statusText}`);
    return res.json();
  },

  async getOtherRecords(taskId: string, companyId?: string | null): Promise<{
    records: {
      id: string;
      record_type: string;
      payload_json: Record<string, unknown>;
      source_file_id?: string | null;
      created_at: string;
    }[];
  }> {
    const res = await apiFetch(`/api/other/records?task_id=${encodeURIComponent(taskId)}`, { companyId });
    if (!res.ok) return { records: [] };
    return res.json();
  },

  async updateOtherRecord(recordId: string, payloadJson: Record<string, unknown>): Promise<{ ok: boolean }> {
    const res = await apiFetch(`/api/other/records/${encodeURIComponent(recordId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ payload_json: payloadJson }),
    });
    if (!res.ok) throw new Error(`Asset record update failed: ${res.statusText}`);
    return res.json();
  },

  async getDepreciationSchedule(recordId: string): Promise<{
    schedule: { period_start: string; period_end: string; depreciation_amount: number; accumulated_at_period_end: number; net_book_value_at_period_end: number }[];
  }> {
    const res = await apiFetch(`/api/other/records/${encodeURIComponent(recordId)}/depreciation`);
    if (!res.ok) return { schedule: [] };
    return res.json();
  },

  // ── Rule Memory API ──────────────────────────────────────────────────────────

  async getRuleMemory(mode: string): Promise<{
    company_id: string; mode: string; content: string; version: number;
    updated_at: string | null; updated_by_user: string | null; updated_by_type: string;
    is_active?: boolean;
  }> {
    const res = await apiFetch(`/company/memory/${encodeURIComponent(mode)}`);
    if (!res.ok) throw new Error(`Failed to load rule memory: ${res.statusText}`);
    return res.json();
  },

  async saveRuleMemory(mode: string, content: string, version: number): Promise<{
    status: string; mode: string; version: number; updated_at: string | null;
  }> {
    const res = await apiFetch(`/company/memory/${encodeURIComponent(mode)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, version }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail?.message || err.detail || res.statusText);
    }
    return res.json();
  },

  async patchRuleMemoryActive(mode: string, is_active: boolean): Promise<{
    status: string; mode: string; is_active: boolean; version: number;
  }> {
    const res = await apiFetch(`/company/memory/${encodeURIComponent(mode)}/active`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_active }),
    });
    if (!res.ok) throw new Error(`Update skill active failed: ${res.statusText}`);
    return res.json();
  },

  async getRuleMemoryHistory(mode: string): Promise<Array<{
    version: number; saved_at: string | null; saved_by: string | null;
    saved_by_type: string; content_preview: string;
  }>> {
    const res = await apiFetch(`/company/memory/${encodeURIComponent(mode)}/history`);
    if (!res.ok) return [];
    return res.json();
  },

  async restoreRuleMemoryVersion(mode: string, version: number): Promise<{ status: string; new_version: number }> {
    const res = await apiFetch(`/company/memory/${encodeURIComponent(mode)}/restore/${version}`, { method: 'POST' });
    if (!res.ok) throw new Error(`Restore failed: ${res.statusText}`);
    return res.json();
  },

  async generateRuleMemory(mode: string, businessDescription: string, companyName?: string): Promise<{
    status: string; mode: string; version: number; content: string;
  }> {
    const res = await apiFetch(`/company/memory/${encodeURIComponent(mode)}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ business_description: businessDescription, company_name: companyName }),
    });
    if (!res.ok) throw new Error(`Generate failed: ${res.statusText}`);
    return res.json();
  },

  // ── Company Manual API ────────────────────────────────────────────────────

  async getCompanyManual(): Promise<{
    company_id: string; content: string; version: number;
    updated_at: string | null; updated_by_type: string;
  }> {
    const res = await apiFetch('/company/manual');
    if (!res.ok) throw new Error(`Failed to load company manual: ${res.statusText}`);
    return res.json();
  },

  async companyManualExists(): Promise<{ exists: boolean; wizardCompleted: boolean }> {
    const res = await apiFetch('/company/manual/exists');
    if (!res.ok) return { exists: false, wizardCompleted: false };
    const data = await res.json();
    return {
      exists: data.exists === true,
      wizardCompleted: data.wizard_completed === true,
    };
  },

  async saveCompanyManual(content: string, version: number): Promise<{
    status: string; version: number; updated_at: string | null;
  }> {
    const res = await apiFetch('/company/manual', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, version }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail?.message || err.detail || res.statusText);
    }
    return res.json();
  },

  async getCompanyManualHistory(): Promise<Array<{
    version: number; saved_at: string | null; saved_by: string | null;
    saved_by_type: string; content_preview: string;
  }>> {
    const res = await apiFetch('/company/manual/history');
    if (!res.ok) return [];
    return res.json();
  },

  async restoreCompanyManualVersion(version: number): Promise<{ status: string; new_version: number }> {
    const res = await apiFetch(`/company/manual/restore/${version}`, { method: 'POST' });
    if (!res.ok) throw new Error(`Restore failed: ${res.statusText}`);
    return res.json();
  },

  // ── Exclusion Rules API ───────────────────────────────────────────────────

  async listExclusionRules(): Promise<Array<{
    id: string; pattern: string; pattern_type: string; reason: string | null;
    modes: string | null; is_active: boolean; hit_count: number;
    last_hit_at: string | null; created_at: string | null;
  }>> {
    const res = await apiFetch('/company/exclusions');
    if (!res.ok) throw new Error(`Failed to load exclusion rules: ${res.statusText}`);
    return res.json();
  },

  async createExclusionRule(data: {
    pattern: string; pattern_type: string; reason?: string; modes?: string;
  }): Promise<{ id: string; pattern: string; pattern_type: string }> {
    const res = await apiFetch('/company/exclusions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  },

  async updateExclusionRule(id: string, data: {
    pattern?: string; pattern_type?: string; reason?: string; modes?: string; is_active?: boolean;
  }): Promise<{ id: string }> {
    const res = await apiFetch(`/company/exclusions/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(`Update failed: ${res.statusText}`);
    return res.json();
  },

  async deleteExclusionRule(id: string): Promise<void> {
    const res = await apiFetch(`/company/exclusions/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`Delete failed: ${res.statusText}`);
  },

  // ── Company classification rules (reconciliation AI hints) ────────────────

  async listClassificationRules(): Promise<ClassificationRuleApiRow[]> {
    const res = await apiFetch('/company/classification-rules');
    if (!res.ok) throw new Error(`Failed to load classification rules: ${res.statusText}`);
    const data = await res.json();
    return Array.isArray(data.rules) ? data.rules : [];
  },

  async createClassificationRule(payload: {
    rule_type?: 'company_custom' | 'knowledge_article'
    rule_name: string
    pattern_type?: 'keyword' | 'vendor' | 'amount'
    pattern?: string
    use_when?: string
    content?: string
    notes?: string
    document_type?: string
    priority?: number
  }): Promise<ClassificationRuleApiRow> {
    const res = await apiFetch('/company/classification-rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  },

  async patchClassificationRule(
    id: string,
    payload: {
      rule_name?: string
      pattern_type?: 'keyword' | 'vendor' | 'amount'
      pattern?: string
      notes?: string
      use_when?: string
      content?: string
      document_type?: string
      is_active?: boolean
      priority?: number
    },
  ): Promise<ClassificationRuleApiRow> {
    const res = await apiFetch(`/company/classification-rules/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  },

  async deleteClassificationRule(id: string): Promise<void> {
    const res = await apiFetch(`/company/classification-rules/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
  },

  async upsertCompanyKnowledgeContext(body: string, use_when?: string | null): Promise<ClassificationRuleApiRow> {
    const res = await apiFetch('/company/classification-rules/company-context', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        body,
        use_when: use_when === undefined ? undefined : use_when || null,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  },

};

// ── Server-side task shape (snake_case from API) ───────────────────────────
export interface ServerChatTask {
  id: string;
  company_id: string;
  owner_user_id: string;
  title: string;
  processing_mode: string;
  status: string;
  is_shared_to_company: boolean;
  file_count: number;
  page_count: number;
  has_spreadsheet: boolean;
  bank_batch_ids: string[] | null;
  ledger_batch_ids: string[] | null;
  dup_warning: string | null;
  title_generated: boolean;
  created_at: string;
  updated_at: string;
}

export interface ServerTaskMessage {
  id: string;
  task_id: string;
  sequence_index: number;
  role: string;
  content_text: string;
  content_type: string;
  payload_json: unknown;
  created_at: string;
}

export interface CreateTaskBody {
  id?: string;
  title: string;
  processing_mode: string;
  status?: string;
  file_count?: number;
  page_count?: number;
  has_spreadsheet?: boolean;
  bank_batch_ids?: string[] | null;
  ledger_batch_ids?: string[] | null;
  dup_warning?: string | null;
  title_generated?: boolean;
}

export interface PatchTaskBody {
  title?: string;
  processing_mode?: string;
  status?: string;
  file_count?: number;
  page_count?: number;
  has_spreadsheet?: boolean;
  bank_batch_ids?: string[] | null;
  ledger_batch_ids?: string[] | null;
  dup_warning?: string | null;
  title_generated?: boolean;
}

/** Best-effort parse of FastAPI `detail` from a failed Response (clone so body can still be read). */
async function formatApiFailure(res: Response, label: string): Promise<string> {
  const head = `${label} (${res.status} ${res.statusText || ''})`.trim();
  try {
    const data = (await res.clone().json()) as { detail?: unknown };
    const d = data.detail;
    if (typeof d === 'string' && d.trim()) return `${head}: ${d.trim()}`.trim();
    if (Array.isArray(d) && d.length) {
      const msgs = d
        .map((x) =>
          typeof x === 'object' && x && 'msg' in x ? String((x as { msg: unknown }).msg) : JSON.stringify(x),
        )
        .filter(Boolean);
      if (msgs.length) return `${head}: ${msgs.join('; ')}`.trim();
    }
  } catch {
    /* non-JSON or empty body */
  }
  return head;
}

// ── Task CRUD API ──────────────────────────────────────────────────────────
export const taskApi = {
  async list(): Promise<ServerChatTask[]> {
    const res = await apiFetch('/api/tasks', { timeoutMs: 45_000 });
    if (!res.ok) throw new Error(`Tasks list failed: ${res.statusText}`);
    return res.json();
  },

  async get(id: string, companyId?: string | null): Promise<ServerChatTask> {
    const res = await apiFetch(`/api/tasks/${encodeURIComponent(id)}`, { companyId });
    if (!res.ok) throw new Error(`Task get failed: ${res.statusText}`);
    return res.json();
  },

  async create(body: CreateTaskBody, companyId?: string | null): Promise<ServerChatTask> {
    const res = await apiFetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      companyId,
      timeoutMs: 120_000,
    });
    if (!res.ok) throw new Error(await formatApiFailure(res, 'Task create'));
    return res.json();
  },

  async update(id: string, patch: PatchTaskBody, companyId?: string | null): Promise<ServerChatTask> {
    const res = await apiFetch(`/api/tasks/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
      companyId,
    });
    if (!res.ok) throw new Error(`Task update failed: ${res.statusText}`);
    return res.json();
  },

  async remove(id: string): Promise<void> {
    const res = await apiFetch(`/api/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (!res.ok && res.status !== 204) throw new Error(`Task delete failed: ${res.statusText}`);
  },

  async getMessages(taskId: string, companyId?: string | null): Promise<ServerTaskMessage[]> {
    const res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/messages`, {
      companyId,
      timeoutMs: 45_000,
    });
    if (!res.ok) {
      const err = new Error(`Messages load failed: ${res.statusText}`);
      (err as Error & { status?: number }).status = res.status;
      throw err;
    }
    return res.json();
  },

  async appendMessage(taskId: string, msg: {
    role: string;
    content_text: string;
    content_type?: string;
    payload_json?: unknown;
  }, companyId?: string | null): Promise<ServerTaskMessage> {
    const res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(msg),
      companyId,
    });
    if (!res.ok) throw new Error(`Message append failed: ${res.statusText}`);
    return res.json();
  },

  async patchMessage(
    taskId: string,
    messageId: string,
    body: { content_text?: string; payload_json?: unknown },
    companyId?: string | null,
  ): Promise<ServerTaskMessage> {
    const res = await apiFetch(
      `/api/tasks/${encodeURIComponent(taskId)}/messages/${encodeURIComponent(messageId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        companyId,
      },
    );
    if (!res.ok) throw new Error(`Message patch failed: ${res.statusText}`);
    return res.json();
  },

  async uploadFile(
    taskId: string,
    file: File,
    companyId?: string | null,
  ): Promise<{ id: string; original_filename: string; mime_type: string }> {
    const form = new FormData();
    form.append('file', file);
    const res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/files`, {
      method: 'POST',
      body: form,
      companyId,
    });
    if (!res.ok) throw new Error(`File upload failed: ${res.statusText}`);
    return res.json();
  },

  async downloadFile(taskId: string, fileId: string, companyId?: string | null): Promise<Blob> {
    const res = await apiFetch(
      `/api/tasks/${encodeURIComponent(taskId)}/files/${encodeURIComponent(fileId)}/download`,
      { companyId },
    );
    if (!res.ok) throw new Error(`File download failed: ${res.statusText}`);
    const blob = await res.blob();
    const headerType = res.headers.get('Content-Type')?.split(';')[0]?.trim();
    if (headerType && (!blob.type || blob.type === 'application/octet-stream')) {
      return new Blob([blob], { type: headerType });
    }
    return blob;
  },

  /**
   * On-demand JPEG crop for AQ / Table Review preview (auth'd; not a persisted crop asset).
   * Prefer normalized region (0–1). Omitting region returns a downscaled full page.
   */
  async downloadReceiptCrop(
    taskId: string,
    fileId: string,
    opts: {
      page?: number
      regionNorm?: { x: number; y: number; w: number; h: number } | null
      regionBbox?: { x: number; y: number; w: number; h: number } | null
      companyId?: string | null
    } = {},
  ): Promise<Blob> {
    const q = new URLSearchParams()
    if (opts.page != null && opts.page >= 1) q.set('page', String(opts.page))
    const n = opts.regionNorm
    if (n && n.w > 0 && n.h > 0) {
      q.set('x', String(n.x))
      q.set('y', String(n.y))
      q.set('w', String(n.w))
      q.set('h', String(n.h))
    } else if (opts.regionBbox && opts.regionBbox.w > 0 && opts.regionBbox.h > 0) {
      q.set('bx', String(opts.regionBbox.x))
      q.set('by', String(opts.regionBbox.y))
      q.set('bw', String(opts.regionBbox.w))
      q.set('bh', String(opts.regionBbox.h))
    }
    const qs = q.toString()
    const res = await apiFetch(
      `/api/tasks/${encodeURIComponent(taskId)}/files/${encodeURIComponent(fileId)}/receipt-crop${qs ? `?${qs}` : ''}`,
      { companyId: opts.companyId },
    )
    if (!res.ok) throw new Error(`Receipt crop preview failed: ${res.statusText}`)
    const blob = await res.blob()
    if (!blob.type || blob.type === 'application/octet-stream') {
      return new Blob([blob], { type: 'image/jpeg' })
    }
    return blob
  },

  async upsertOcrSnapshot(taskId: string, msg: {
    role: string;
    content_text: string;
    payload_json: unknown;
  }, companyId?: string | null): Promise<ServerTaskMessage> {
    const res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/messages/ocr_snapshot`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...msg, content_type: 'ocr_snapshot' }),
      companyId,
    });
    if (!res.ok) throw new Error(`OCR snapshot upsert failed: ${res.statusText}`);
    return res.json();
  },

  async upsertBatchOcrSnapshot(
    taskId: string,
    uploadBatchId: string,
    msg: {
      role: string;
      content_text: string;
      payload_json: unknown;
    },
    companyId?: string | null,
  ): Promise<ServerTaskMessage> {
    const res = await apiFetch(
      `/api/tasks/${encodeURIComponent(taskId)}/messages/ocr_snapshot/batch/${encodeURIComponent(uploadBatchId)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...msg, content_type: 'ocr_snapshot' }),
        companyId,
      },
    );
    if (!res.ok) throw new Error(`Batch OCR snapshot upsert failed: ${res.statusText}`);
    return res.json();
  },

  /** Full AP OCR from stored task files using primary + cross-VLM in-place merge when configured. */
  async apCrossVerify(taskId: string, body: ApCrossVerifyRequest): Promise<ApCrossVerifyResponse> {
    const res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/ap-cross-verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      timeoutMs: 900_000,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = (err as { detail?: unknown }).detail;
      throw new Error(typeof detail === 'string' ? detail : res.statusText);
    }
    return res.json();
  },

  async saveState(taskId: string, stateType: string, payloadJson: unknown): Promise<void> {
    const res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/state`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state_type: stateType, payload_json: payloadJson }),
    });
    if (!res.ok) throw new Error(`State save failed: ${res.statusText}`);
  },

  async loadState(taskId: string, stateType: string): Promise<unknown> {
    const res = await apiFetch(
      `/api/tasks/${encodeURIComponent(taskId)}/state?state_type=${encodeURIComponent(stateType)}`,
    );
    if (!res.ok) return null;
    const data = await res.json().catch(() => null);
    return data?.payload_json ?? null;
  },

  async toggleShare(taskId: string): Promise<{ is_shared_to_company: boolean }> {
    const res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/share`, {
      method: 'PATCH',
    });
    if (!res.ok) throw new Error(`Share toggle failed: ${res.statusText}`);
    return res.json();
  },
};
