import { useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { getLoginFlowErrorMessage } from '../utils/httpErrorMessage';
import { BookcometLogo } from '../components/BookcometLogo';
import './LoginPage.css';

export default function LoginPage() {
  const { login, completeMfaLogin } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const successMessage = (location.state as { message?: string } | null)?.message || '';

  const workspacePrefetchOnce = useRef(false);
  const warmupWorkspaceChunk = () => {
    if (workspacePrefetchOnce.current) return;
    workspacePrefetchOnce.current = true;
    void import('../features/workspace/WorkspaceApp');
  };

  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const finishToApp = () => {
    navigate('/app', { replace: true });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    warmupWorkspaceChunk();
    setLoading(true);
    try {
      if (mfaToken) {
        await completeMfaLogin(mfaToken, mfaCode);
        finishToApp();
        return;
      }
      const result = await login(identifier, password);
      if (result.mfaRequired) {
        setMfaToken(result.mfaToken);
        setLoading(false);
        return;
      }
      finishToApp();
      /* Omit setLoading(false) on success — avoids flashing Sign In during route + Suspense */
    } catch (err: unknown) {
      setError(getLoginFlowErrorMessage(err));
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-logo">
          <BookcometLogo variant="auth" alt="" />
          <h1>Bookcomet</h1>
          <p>{mfaToken ? 'Enter your authenticator code' : 'Sign in to your account'}</p>
        </div>

        {successMessage && <div className="auth-success">{successMessage}</div>}
        {error && <div className="auth-error">{error}</div>}

        <form className="auth-form" onSubmit={handleSubmit}>
          {!mfaToken ? (
            <>
              <div className="form-group">
                <label htmlFor="identifier">Username or email</label>
                <input
                  id="identifier"
                  type="text"
                  placeholder="username"
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  onFocus={() => warmupWorkspaceChunk()}
                  required
                  autoComplete="username"
                  disabled={loading}
                />
              </div>

              <div className="form-group">
                <label htmlFor="password">Password</label>
                <div className="password-wrapper">
                  <input
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    placeholder="••••••••"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onFocus={() => warmupWorkspaceChunk()}
                    required
                    autoComplete="current-password"
                    disabled={loading}
                  />
                  <button
                    type="button"
                    className="password-toggle"
                    onClick={() => setShowPassword((v) => !v)}
                    tabIndex={-1}
                  >
                    {showPassword ? 'Hide' : 'Show'}
                  </button>
                </div>
              </div>
            </>
          ) : (
            <div className="form-group">
              <label htmlFor="mfaCode">Authentication code</label>
              <input
                id="mfaCode"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="6-digit code"
                value={mfaCode}
                onChange={(e) => setMfaCode(e.target.value)}
                required
                disabled={loading}
              />
              <p className="form-hint">
                Open your authenticator app (SEC-CODE-009).{' '}
                <button
                  type="button"
                  className="auth-inline-link"
                  onClick={() => {
                    setMfaToken(null);
                    setMfaCode('');
                    setError('');
                  }}
                >
                  Back to password
                </button>
              </p>
            </div>
          )}

          <button type="submit" className="auth-btn" disabled={loading}>
            {loading ? 'Signing in…' : mfaToken ? 'Verify code' : 'Sign In'}
          </button>
          {loading ? (
            <p className="auth-signin-hint" aria-live="polite">
              Signing in and loading your workspace…
            </p>
          ) : null}
        </form>

        <div className="auth-divider">Don't have an account?</div>
        <div className="auth-switch">
          <Link to="/register">Create an account</Link>
        </div>
      </div>
    </div>
  );
}
