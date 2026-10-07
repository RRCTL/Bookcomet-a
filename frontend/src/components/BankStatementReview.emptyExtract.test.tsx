import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BankStatementReview } from './BankStatementReview'
import {
  EMPTY_EXTRACT_BODY,
  EMPTY_EXTRACT_MARK_ACTION,
  EMPTY_EXTRACT_MARK_CONFIRM,
  EMPTY_EXTRACT_RETRY_ACTION,
  EMPTY_EXTRACT_TITLE,
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

  it('shows Mark as reviewed with no rows only after auto-retry, with confirm', () => {
    const onMark = vi.fn()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
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
    expect(confirmSpy).toHaveBeenCalledWith(EMPTY_EXTRACT_MARK_CONFIRM)
    expect(onMark).toHaveBeenCalledTimes(1)
    confirmSpy.mockRestore()
  })
})
