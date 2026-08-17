import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { authApi } from '../services/authApi';
import { api } from '../services/api';
import { getLoginFlowErrorMessage } from '../utils/httpErrorMessage';
import { BookcometLogo } from '../components/BookcometLogo';
import './LoginPage.css';

function passwordStrength(pw: string): { score: number; label: string; color: string } {
  let score = 0;
  if (pw.length >= 8) score++;
  if (/[A-Z]/.test(pw)) score++;
  if (/[0-9]/.test(pw)) score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;
  const labels = ['', 'Weak', 'Fair', 'Good', 'Strong'];
  const colors = ['', '#ef4444', '#f59e0b', '#22c55e', '#10b981'];
  return { score, label: labels[score] || '', color: colors[score] || '' };
}

export default function RegisterPage() {
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState('');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [inviteRequired, setInviteRequired] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const strength = passwordStrength(password);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const health = await api.healthCheck();
        if (!cancelled) {
          setInviteRequired(Boolean(health.register_invite_required));
        }
      } catch {
        // Keep invite optional in UI if health is unreachable; server still enforces.
        if (!cancelled) setInviteRequired(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    if (!/[A-Za-z]/.test(password)) {
      setError('Password must contain at least one letter.');
      return;
    }
    if (!/\d/.test(password)) {
      setError('Password must contain at least one number.');
      return;
    }
    if (inviteRequired && !inviteCode.trim()) {
      setError('Invite code is required for this server.');
      return;
    }
    setLoading(true);
    try {
      await authApi.register(
        username,
        displayName,
        password,
        email || null,
        inviteCode || null,
      );
      navigate('/login', {
        replace: true,
        state: { message: 'Account created. Sign in with your username.' },
      });
    } catch (err: unknown) {
      setError(getLoginFlowErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-logo">
          <BookcometLogo variant="auth" alt="" />
          <h1>Create account</h1>
          <p>Get started with Bookcomet</p>
        </div>

        {error && <div className="auth-error">{error}</div>}

        <form className="auth-form" onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="displayName">Display name</label>
            <input
              id="displayName"
              type="text"
              placeholder="Jane Smith"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              required
              autoComplete="name"
            />
          </div>

          <div className="form-group">
            <label htmlFor="username">Username</label>
            <input
              id="username"
              type="text"
              placeholder="jane"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoComplete="username"
              minLength={3}
              maxLength={64}
              pattern="[A-Za-z0-9._\-]{3,64}"
              title="3–64 characters: letters, numbers, dots, underscores, or hyphens"
            />
          </div>

          <div className="form-group">
            <label htmlFor="email">Email (optional)</label>
            <input
              id="email"
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </div>

          {inviteRequired && (
            <div className="form-group">
              <label htmlFor="inviteCode">Invite code</label>
              <input
                id="inviteCode"
                type="text"
                placeholder="From the person who runs this server"
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                required
                autoComplete="one-time-code"
              />
              <p className="form-hint">
                Ask the host for the code in their <code>backend/.env</code>{' '}
                (<code>REGISTER_INVITE_CODE</code>). Pure localhost setups leave
                it empty and skip this field.
              </p>
            </div>
          )}

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <div className="password-wrapper">
              <input
                id="password"
                type={showPassword ? 'text' : 'password'}
                placeholder="8+ chars, one letter & one number"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="new-password"
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
            {password && (
              <>
                <div className="strength-meter">
                  <div
                    className="strength-fill"
                    style={{ width: `${(strength.score / 4) * 100}%`, background: strength.color }}
                  />
                </div>
                <div className="strength-label" style={{ color: strength.color }}>
                  {strength.label}
                </div>
              </>
            )}
          </div>

          <div className="form-group">
            <label htmlFor="confirmPassword">Confirm password</label>
            <input
              id="confirmPassword"
              type={showPassword ? 'text' : 'password'}
              placeholder="••••••••"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              autoComplete="new-password"
            />
          </div>

          <button type="submit" className="auth-btn" disabled={loading}>
            {loading ? 'Creating account…' : 'Create Account'}
          </button>
        </form>

        <div className="auth-switch" style={{ marginTop: 20 }}>
          Already have an account?
          <Link to="/login">Sign in</Link>
        </div>
      </div>
    </div>
  );
}
