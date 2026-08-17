import axios from 'axios';

import { API_BASE_URL } from '../config/apiBase';

export { API_BASE_URL };

/** Avoid aborting bcrypt + DB + WAN/tunnel hops; 30s was too aggressive for cold/slow backends. */
const AUTH_HTTP_TIMEOUT_MS = 90_000;

export const authHttp = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true, // send httpOnly cookies on every request
  timeout: AUTH_HTTP_TIMEOUT_MS,
});

export interface AuthUser {
  id: string;
  username: string;
  email: string | null;
  display_name: string;
  is_verified: boolean;
  mfa_enabled?: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export type LoginResult =
  | { mfaRequired: false; accessToken: string }
  | { mfaRequired: true; mfaToken: string };

export interface MfaSetupResponse {
  secret: string;
  otpauth_url: string;
  mfa_enabled: boolean;
}

export const authApi = {
  async register(
    username: string,
    display_name: string,
    password: string,
    email?: string | null,
    invite_code?: string | null,
  ): Promise<{ message: string }> {
    const body: Record<string, string> = { username, display_name, password };
    if (email && email.trim()) body.email = email.trim();
    if (invite_code && invite_code.trim()) body.invite_code = invite_code.trim();
    const { data } = await authHttp.post('/auth/register', body);
    return data;
  },

  async login(identifier: string, password: string): Promise<LoginResult> {
    const { data } = await authHttp.post<{
      access_token?: string | null;
      mfa_required?: boolean;
      mfa_token?: string | null;
    }>('/auth/login', { identifier, password });
    if (data.mfa_required) {
      if (!data.mfa_token) throw new Error('MFA challenge missing from server.');
      return { mfaRequired: true, mfaToken: data.mfa_token };
    }
    if (!data.access_token) throw new Error('Sign-in succeeded but no access token was returned.');
    return { mfaRequired: false, accessToken: data.access_token };
  },

  async verifyMfa(mfaToken: string, code: string): Promise<TokenResponse> {
    const { data } = await authHttp.post<TokenResponse>('/auth/mfa/verify', {
      mfa_token: mfaToken,
      code,
    });
    return data;
  },

  async mfaSetup(accessToken: string): Promise<MfaSetupResponse> {
    const { data } = await authHttp.post<MfaSetupResponse>(
      '/auth/mfa/setup',
      {},
      { headers: { Authorization: `Bearer ${accessToken}` } },
    );
    return data;
  },

  async mfaEnable(accessToken: string, code: string): Promise<{ message: string }> {
    const { data } = await authHttp.post(
      '/auth/mfa/enable',
      { code },
      { headers: { Authorization: `Bearer ${accessToken}` } },
    );
    return data;
  },

  async mfaDisable(
    accessToken: string,
    password: string,
    code: string,
  ): Promise<{ message: string }> {
    const { data } = await authHttp.post(
      '/auth/mfa/disable',
      { password, code },
      { headers: { Authorization: `Bearer ${accessToken}` } },
    );
    return data;
  },

  async revokeSessions(accessToken: string): Promise<{ message: string }> {
    const { data } = await authHttp.post(
      '/auth/revoke-sessions',
      {},
      { headers: { Authorization: `Bearer ${accessToken}` } },
    );
    return data;
  },

  async logout(): Promise<void> {
    await authHttp.post('/auth/logout');
  },

  async refresh(): Promise<TokenResponse> {
    const { data } = await authHttp.post<TokenResponse>('/auth/refresh');
    return data;
  },

  async getMe(accessToken: string): Promise<AuthUser> {
    const { data } = await authHttp.get<AuthUser>('/auth/me', {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    return data;
  },

  async changePassword(accessToken: string, old_password: string, new_password: string): Promise<{ message: string }> {
    const { data } = await authHttp.post(
      '/auth/change-password',
      { old_password, new_password },
      { headers: { Authorization: `Bearer ${accessToken}` } },
    );
    return data;
  },

  async updateProfile(accessToken: string, display_name: string): Promise<AuthUser> {
    const { data } = await authHttp.patch<AuthUser>(
      '/auth/me',
      { display_name },
      { headers: { Authorization: `Bearer ${accessToken}` } },
    );
    return data;
  },
};
