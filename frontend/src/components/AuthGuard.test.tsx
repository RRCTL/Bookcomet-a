import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { AuthGuard, AuthLoadingScreen } from './AuthGuard'

vi.mock('../contexts/AuthContext', () => ({
  useAuth: vi.fn(),
}))

import { useAuth } from '../contexts/AuthContext'

const mockedUseAuth = vi.mocked(useAuth)

describe('AuthGuard', () => {
  it('shows the loading screen while session restore is pending', () => {
    render(<AuthLoadingScreen />)

    expect(screen.getByText('Loading…')).toBeInTheDocument()
  })

  it('redirects unauthenticated users to login', () => {
    mockedUseAuth.mockReturnValue({
      user: null,
      accessToken: null,
      isLoading: false,
      companies: [],
      activeCompany: null,
      needsCompanyPick: false,
      login: vi.fn(),
      completeMfaLogin: vi.fn(),
      logout: vi.fn(),
      switchCompany: vi.fn(),
      setAccessToken: vi.fn(),
      refreshCompanies: vi.fn(),
      applyUser: vi.fn(),
    })

    render(
      <MemoryRouter initialEntries={['/workspace']}>
        <Routes>
          <Route path="/login" element={<div>Login Page</div>} />
          <Route path="/workspace" element={<AuthGuard><div>Workspace</div></AuthGuard>} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText('Login Page')).toBeInTheDocument()
  })
})
