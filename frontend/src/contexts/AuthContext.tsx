import axios, { AxiosHeaders } from 'axios';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { authApi, type AuthUser, API_BASE_URL } from '../services/authApi';
import { getLoginFlowErrorMessage } from '../utils/httpErrorMessage';
import {
  clearAuthRefreshCooldown,
  markAuthRefreshCooldown,
  setAccessTokenGetter,
  setApiRefreshFailureHandler,
  setCompanyIdGetter,
  setTokenRefresher,
} from '../services/api';

export interface UserCompany {
  id: string;
  name: string;
  role: string;
  roleLabel: 'Admin' | 'Member';
}

interface AuthContextType {
  user: AuthUser | null;
  accessToken: string | null;
  isLoading: boolean;
  companies: UserCompany[];
  activeCompany: UserCompany | null;
  needsCompanyPick: boolean;
  login: (
    identifier: string,
    password: string,
  ) => Promise<{ mfaRequired: true; mfaToken: string } | { mfaRequired: false }>;
  completeMfaLogin: (mfaToken: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
  switchCompany: (companyId: string) => void;
  setAccessToken: (token: string | null) => void;
  refreshCompanies: () => Promise<void>;
  applyUser: (profile: AuthUser) => void;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}

function mapRole(role: string): 'Admin' | 'Member' {
  return role === 'owner' ? 'Admin' : 'Member';
}

async function fetchCompanies(accessToken: string): Promise<UserCompany[]> {
  const resp = await fetch(`${API_BASE_URL}/companies/mine`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    credentials: 'include',
  });
  if (!resp.ok) return [];
  const data: Array<{ id: string; name: string; role: string }> = await resp.json();
  return data.map((c) => ({
    id: c.id,
    name: c.name,
    role: c.role,
    roleLabel: mapRole(c.role),
  }));
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [accessToken, setAccessTokenState] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [companies, setCompanies] = useState<UserCompany[]>([]);
  const [activeCompany, setActiveCompany] = useState<UserCompany | null>(null);
  const [needsCompanyPick, setNeedsCompanyPick] = useState(false);

  const tokenRef = useRef<string | null>(null);
  tokenRef.current = accessToken;

  const activeCompanyRef = useRef<UserCompany | null>(null);
  activeCompanyRef.current = activeCompany;

  const setAccessToken = useCallback((token: string | null) => {
    setAccessTokenState(token);
    tokenRef.current = token;
  }, []);

  useEffect(() => {
    setAccessTokenGetter(() => tokenRef.current);
    setCompanyIdGetter(() => activeCompanyRef.current?.id ?? 'default');
    setTokenRefresher(async () => {
      const { access_token } = await authApi.refresh();
      clearAuthRefreshCooldown();
      setAccessToken(access_token);
      return access_token;
    });
  }, [setAccessToken]);

  // Align fetch(apiFetch) with axios: on refresh failure, clear session once (stops idle poll 401 spam).
  useEffect(() => {
    setApiRefreshFailureHandler(() => {
      void authApi.logout().catch(() => {});
      setUser(null);
      setAccessToken(null);
      setCompanies([]);
      setActiveCompany(null);
      activeCompanyRef.current = null;
      setNeedsCompanyPick(false);
      localStorage.removeItem('activeCompanyId');
    });
    return () => setApiRefreshFailureHandler(null);
  }, [setAccessToken]);

  const resolveActiveCompany = useCallback((list: UserCompany[]) => {
    if (list.length === 0) return;
    const savedId = localStorage.getItem('activeCompanyId');
    const saved = savedId ? list.find((c) => c.id === savedId) : null;
    if (saved) {
      setActiveCompany(saved);
      activeCompanyRef.current = saved;
      setNeedsCompanyPick(false);
    } else if (list.length === 1) {
      setActiveCompany(list[0]);
      activeCompanyRef.current = list[0];
      localStorage.setItem('activeCompanyId', list[0].id);
      setNeedsCompanyPick(false);
    } else {
      setNeedsCompanyPick(true);
    }
  }, []);

  const refreshCompanies = useCallback(async () => {
    const token = tokenRef.current;
    if (!token) return;
    const list = await fetchCompanies(token);
    setCompanies(list);
    resolveActiveCompany(list);
  }, [resolveActiveCompany]);

  // Silent session restore on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { access_token } = await authApi.refresh();
        clearAuthRefreshCooldown();
        if (cancelled) return;
        const profile = await authApi.getMe(access_token);
        if (cancelled) return;
        setAccessToken(access_token);
        setUser(profile);
        const list = await fetchCompanies(access_token);
        if (cancelled) return;
        setCompanies(list);
        resolveActiveCompany(list);
      } catch {
        // No valid session — stay on login
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [setAccessToken, resolveActiveCompany]);

  // Axios interceptor: attach Bearer token + 401 → auto-refresh + retry
  useEffect(() => {
    const requestId = axios.interceptors.request.use((config) => {
      const token = tokenRef.current;
      if (token) {
        config.headers = AxiosHeaders.from(config.headers);
        config.headers['Authorization'] = `Bearer ${token}`;
        if (!config.headers['X-Company-ID']) {
          config.headers['X-Company-ID'] = activeCompanyRef.current?.id ?? 'default';
        }
      }
      return config;
    });

    let isRefreshing = false;
    let queue: Array<(t: string) => void> = [];

    const responseId = axios.interceptors.response.use(
      (res) => res,
      async (error) => {
        const original = error.config;
        if (error.response?.status !== 401 || original._retry) {
          return Promise.reject(error);
        }
        if (isRefreshing) {
          return new Promise((resolve) => {
            queue.push((t) => {
              original.headers['Authorization'] = `Bearer ${t}`;
              resolve(axios(original));
            });
          });
        }
        original._retry = true;
        isRefreshing = true;
        try {
          const { access_token } = await authApi.refresh();
          setAccessToken(access_token);
          queue.forEach((cb) => cb(access_token));
          queue = [];
          original.headers['Authorization'] = `Bearer ${access_token}`;
          return axios(original);
        } catch {
          markAuthRefreshCooldown();
          setUser(null);
          setAccessToken(null);
          return Promise.reject(error);
        } finally {
          isRefreshing = false;
        }
      },
    );

    return () => {
      axios.interceptors.request.eject(requestId);
      axios.interceptors.response.eject(responseId);
    };
  }, [setAccessToken]);

  const finishLoginWithAccessToken = useCallback(async (access_token: string) => {
    let profile: AuthUser;
    try {
      profile = await authApi.getMe(access_token);
    } catch (e) {
      throw new Error(`Sign-in succeeded but we could not load your profile. ${getLoginFlowErrorMessage(e)}`);
    }
    setAccessToken(access_token);
    setUser(profile);
    clearAuthRefreshCooldown();
    try {
      const list = await fetchCompanies(access_token);
      setCompanies(list);
      resolveActiveCompany(list);
    } catch (e) {
      throw new Error(`Sign-in succeeded but we could not load your companies. ${getLoginFlowErrorMessage(e)}`);
    }
  }, [setAccessToken, resolveActiveCompany]);

  const login = useCallback(async (identifier: string, password: string) => {
    const result = await authApi.login(identifier, password);
    if (result.mfaRequired) {
      return { mfaRequired: true as const, mfaToken: result.mfaToken };
    }
    await finishLoginWithAccessToken(result.accessToken);
    return { mfaRequired: false as const };
  }, [finishLoginWithAccessToken]);

  const completeMfaLogin = useCallback(async (mfaToken: string, code: string) => {
    const { access_token } = await authApi.verifyMfa(mfaToken, code);
    await finishLoginWithAccessToken(access_token);
  }, [finishLoginWithAccessToken]);

  const logout = useCallback(async () => {
    try { await authApi.logout(); } catch { /* ignore */ }
    clearAuthRefreshCooldown();
    setUser(null);
    setAccessToken(null);
    setCompanies([]);
    setActiveCompany(null);
    activeCompanyRef.current = null;
    setNeedsCompanyPick(false);
    localStorage.removeItem('activeCompanyId');
  }, [setAccessToken]);

  const switchCompany = useCallback((companyId: string) => {
    const found = companies.find((c) => c.id === companyId);
    if (!found) return;
    setActiveCompany(found);
    activeCompanyRef.current = found;
    localStorage.setItem('activeCompanyId', companyId);
    setNeedsCompanyPick(false);
  }, [companies]);

  const applyUser = useCallback((profile: AuthUser) => {
    setUser(profile);
  }, []);

  return (
    <AuthContext.Provider value={{
      user,
      accessToken,
      isLoading,
      companies,
      activeCompany,
      needsCompanyPick,
      login,
      completeMfaLogin,
      logout,
      switchCompany,
      setAccessToken,
      refreshCompanies,
      applyUser,
    }}>
      {children}
    </AuthContext.Provider>
  );
}
