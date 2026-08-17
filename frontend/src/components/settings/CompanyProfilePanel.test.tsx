import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CompanyProfilePanel } from './CompanyProfilePanel'

const settings = {
  companies: [{ id: 'ws-1', name: 'Alpha', role: 'owner', roleLabel: 'Admin' }],
  activeCompany: { id: 'ws-1', name: 'Alpha', role: 'owner', roleLabel: 'Admin' },
  switchCompany: vi.fn(),
  workspaceErr: '',
  setWorkspaceErr: vi.fn(),
  workspaceSearch: '',
  setWorkspaceSearch: vi.fn(),
  workspaceAddName: '',
  setWorkspaceAddName: vi.fn(),
  workspaceAddBusy: false,
  addWorkspace: vi.fn(),
  activeIsOwner: true,
  filteredWorkspaces: [{ id: 'ws-1', name: 'Alpha', role: 'owner', roleLabel: 'Admin' }],
  deleteWorkspace: null,
  setDeleteWorkspace: vi.fn(),
  deleteWorkspaceConfirm: '',
  setDeleteWorkspaceConfirm: vi.fn(),
  deleteWorkspaceBusy: false,
  confirmDeleteWorkspace: vi.fn(),
  manualLoading: false,
  manualError: '',
  manualContent: '',
  manualVersion: 1,
  manualUpdatedAt: null,
  manualEditMode: false,
  setManualEditMode: vi.fn(),
  manualDraft: '',
  setManualDraft: vi.fn(),
  manualSaveStatus: 'idle',
  handleSaveManual: vi.fn(),
  startEditCompanyKnowledge: vi.fn(),
  contextRule: null,
  classificationLoading: false,
  onOpenWizard: vi.fn(),
  wizardCompleted: false,
}

vi.mock('./SettingsProvider', () => ({
  useSettings: () => settings,
}))

describe('CompanyProfilePanel setup wizard button', () => {
  it('shows Setup wizard to the left of Add company knowledge until this workspace finishes', () => {
    settings.wizardCompleted = false
    settings.manualContent = ''
    settings.contextRule = null
    render(<CompanyProfilePanel variant="setup" />)
    const wizard = screen.getByRole('button', { name: 'Setup wizard' })
    const add = screen.getByRole('button', { name: 'Add company knowledge' })
    expect(wizard.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('keeps Setup wizard visible when knowledge already exists', () => {
    settings.wizardCompleted = false
    settings.manualContent = 'Existing notes'
    render(<CompanyProfilePanel variant="setup" />)
    expect(screen.getByRole('button', { name: 'Setup wizard' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
  })

  it('hides Setup wizard after this workspace wizard succeeds', () => {
    settings.wizardCompleted = true
    settings.manualContent = 'Generated knowledge'
    render(<CompanyProfilePanel variant="setup" />)
    expect(screen.queryByRole('button', { name: 'Setup wizard' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
  })
})
