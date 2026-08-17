import { type ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

/** Full-viewport loading state — reused by AuthGuard and lazy-loaded workspace shell. */
export function AuthLoadingScreen() {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100vh',
      background: '#f3f4f6',
      color: '#6b7280',
      fontSize: '13px',
      letterSpacing: '0.01em',
      fontFamily: 'inherit',
    }}>
      Loading…
    </div>
  );
}

export function AuthGuard({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  if (isLoading) return <AuthLoadingScreen />;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
