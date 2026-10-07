import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BankStatementReview } from './BankStatementReview'
import {
  EMPTY_EXTRACT_BODY,
  EMPTY_EXTRACT_MARK_ACTION,
  EMPTY_EXTRACT_MARK_CONFIRM,
  EMPTY_EXTRACT_RETRY_ACTION,
  EMPTY_EXTRACT_TITLE,
  VIEW_PDF_ACTION,
} from '../utils/bankEmptyExtract'

vi.mock('../hooks/useViewport', () => ({
  useViewport: () => ({ isMobile: false }),
}))

describe('BankStatementReview empty-extract amber flags', () => {
  it('shows exact extract-miss copy, Retry this page, and disables Approve', () => {
    const onApprove = vi.fn()
    const onRetry = vi.fn()
    render(
      <BankStatementReview
        transactions={[
          {
            date: '2020-08-01',
            currency: 'HKD',
            deposit: null,
            withdrawal: null,
            balance: 1000,
            particulars: 'Fictional opening',
            account_type: 'HKD CURRENT',
          },
        ]}
        onApprove={onApprove}
        canApprove={true}
        emptyExtractPages={[
          {
            page: 2,
            taskFileId: 'tf-fictional',
            filename: 'Fictional-Statement.pdf',
            autoRetried: false,
            title: EMPTY_EXTRACT_TITLE,
            body: EMPTY_EXTRACT_BODY,
          },
        ]}
        onRetryEmptyExtractPage={onRetry}
      />,
    )

    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('data-empty-extract', 'true')
    expect(status).toHaveTextContent(EMPTY_EXTRACT_TITLE)
    expect(status).toHaveTextContent(EMPTY_EXTRACT_BODY)
    expect(status).toHaveTextContent('not treated as blank')
    expect(screen.queryByText(EMPTY_EXTRACT_MARK_ACTION)).not.toBeInTheDocument()

    const approve = screen.getByRole('button', { name: 'Approve' })
    expect(approve).toBeDisabled()
    fireEvent.click(approve)
    expect(onApprove).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: EMPTY_EXTRACT_RETRY_ACTION }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('shows View PDF link that stays enabled and opens the run file page', () => {
    const onView = vi.fn()
    render(
      <BankStatementReview
        transactions={[]}
        onApprove={() => undefined}
        canApprove={true}
        emptyExtractBusy={false}
        emptyExtractPages={[
          {
            page: 2,
            taskFileId: 'tf-fictional',
            filename: 'Fictional-Statement.pdf',
            autoRetried: true,
            title: EMPTY_EXTRACT_TITLE,
            body: EMPTY_EXTRACT_BODY,
          },
        ]}
        onViewEmptyExtractPdf={onView}
        onRetryEmptyExtractPage={() => undefined}
        onMarkEmptyExtractReviewed={() => undefined}
      />,
    )
    const viewButtons = screen.getAllByRole('button', { name: VIEW_PDF_ACTION })
    expect(viewButtons.length).toBeGreaterThan(0)
    expect(viewButtons[0]).not.toBeDisabled()
    const filenameLink = screen.getByRole('button', { name: 'Fictional-Statement.pdf' })
    expect(filenameLink).not.toBeDisabled()
    fireEvent.click(filenameLink)
    expect(onView).toHaveBeenCalledWith(
      expect.objectContaining({ taskFileId: 'tf-fictional', page: 2 }),
    )
  })

  it('uses in-app Confirm dialog for Mark as reviewed (Cancel / Confirm)', () => {
    const onMark = vi.fn()
    const confirmSpy = vi.spyOn(window, 'confirm')
    render(
      <BankStatementReview
        transactions={[]}
        onApprove={() => undefined}
        canApprove={true}
        emptyExtractPages={[
          {
            page: 3,
            taskFileId: 'tf-fictional',
            filename: 'Fictional-Statement.pdf',
            autoRetried: true,
            title: EMPTY_EXTRACT_TITLE,
            body: EMPTY_EXTRACT_BODY,
          },
        ]}
        onRetryEmptyExtractPage={() => undefined}
        onMarkEmptyExtractReviewed={onMark}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: EMPTY_EXTRACT_MARK_ACTION }))
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toHaveTextContent(EMPTY_EXTRACT_MARK_CONFIRM)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onMark).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: EMPTY_EXTRACT_MARK_ACTION }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(onMark).toHaveBeenCalledTimes(1)
    confirmSpy.mockRestore()
  })
})
