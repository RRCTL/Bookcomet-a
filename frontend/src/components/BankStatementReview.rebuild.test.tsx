import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BankStatementReview } from './BankStatementReview'
import {
  clearReviewTableForRebuild,
  resolveDisplayRowsForRun,
  reviewTableShowingLoader,
} from '../features/erpShell/reviewTableSession'

vi.mock('../hooks/useViewport', () => ({
  useViewport: () => ({ isMobile: false }),
}))

const cleanRow = {
  date: '2024-06-01',
  currency: 'HKD',
  deposit: 100,
  withdrawal: null,
  balance: 500,
  particulars: 'fictional clean row',
  account_type: 'HKD CURRENT',
  source_file: 'Fictional-A.pdf P1',
}

const bleedRow = {
  date: '2024-06-02',
  currency: 'HKD',
  deposit: 50,
  withdrawal: null,
  balance: 200,
  particulars: 'fictional bleed row from another file',
  account_type: 'HKD CURRENT',
  source_file: 'Foreign-Jan.pdf P1',
}

describe('BankStatementReview rebuild from run files', () => {
  it('renders the Rebuild from this run\'s files action', () => {
    render(
      <BankStatementReview
        transactions={[cleanRow]}
        onApprove={() => undefined}
        canApprove={true}
        onRebuildFromRunFiles={() => undefined}
        canRebuild={true}
      />,
    )
    expect(
      screen.getByRole('button', { name: "Rebuild from this run's files" }),
    ).toBeInTheDocument()
  })

  it('disables Rebuild and Approve while rebuild is in progress', () => {
    const onRebuild = vi.fn()
    const onApprove = vi.fn()
    render(
      <BankStatementReview
        transactions={[cleanRow]}
        onApprove={onApprove}
        canApprove={true}
        approveBusy={false}
        onRebuildFromRunFiles={onRebuild}
        canRebuild={true}
        rebuildBusy={true}
      />,
    )

    const rebuild = screen.getByRole('button', { name: 'Rebuilding…' })
    const approve = screen.getByRole('button', { name: 'Approve' })
    expect(rebuild).toBeDisabled()
    expect(approve).toBeDisabled()
    fireEvent.click(rebuild)
    fireEvent.click(approve)
    expect(onRebuild).not.toHaveBeenCalled()
    expect(onApprove).not.toHaveBeenCalled()
  })

  it('clears old rows when rebuild starts (empty loader, no bleed flash)', () => {
    const prevRows = [cleanRow, bleedRow]
    const cleared = clearReviewTableForRebuild('run-fictional')
    expect(cleared.loading).toBe(true)
    expect(cleared.payloads).toEqual({})
    expect(cleared.editedRows).toBeNull()
    expect(reviewTableShowingLoader('run-fictional', cleared)).toBe(true)
    expect(
      resolveDisplayRowsForRun(
        'run-fictional',
        cleared.boundRunId,
        prevRows,
        cleared.editedRows as never,
        true,
      ),
    ).toEqual([])

    const { rerender } = render(
      <BankStatementReview
        transactions={prevRows}
        onApprove={() => undefined}
        canApprove={true}
        onRebuildFromRunFiles={() => undefined}
        canRebuild={true}
        rebuildBusy={false}
      />,
    )
    expect(screen.getByText('fictional bleed row from another file')).toBeInTheDocument()

    // Parent clears transactions as soon as rebuild begins.
    rerender(
      <BankStatementReview
        transactions={[]}
        onApprove={() => undefined}
        canApprove={false}
        onRebuildFromRunFiles={() => undefined}
        canRebuild={false}
        rebuildBusy={true}
      />,
    )
    expect(screen.queryByText('fictional bleed row from another file')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rebuilding…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
  })
})
