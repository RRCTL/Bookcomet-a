import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BankStatementReview } from './BankStatementReview'
import { BANK_ROW_CHECK_MESSAGE } from '../utils/bankRowFieldCheck'

vi.mock('../hooks/useViewport', () => ({
  useViewport: () => ({ isMobile: false }),
}))

describe('BankStatementReview placement flags', () => {
  it('shows amber check message, count banner, and disables Approve', () => {
    const onApprove = vi.fn()
    render(
      <BankStatementReview
        transactions={[
          {
            date: '',
            currency: '2020-08-11',
            deposit: null,
            withdrawal: 4600,
            balance: null,
            particulars: 'fictional shifted row',
            account_type: 'HKD CURRENT',
          },
          {
            date: '2020-08-12',
            currency: 'HKD',
            deposit: 100,
            withdrawal: null,
            balance: 500,
            particulars: 'fictional ok row',
            account_type: 'HKD CURRENT',
          },
        ]}
        onApprove={onApprove}
        canApprove={true}
      />,
    )

    expect(screen.getByRole('status')).toHaveTextContent('1 row needs checking')
    expect(screen.getByText(BANK_ROW_CHECK_MESSAGE)).toBeInTheDocument()
    const approve = screen.getByRole('button', { name: 'Approve' })
    expect(approve).toBeDisabled()
    fireEvent.click(approve)
    expect(onApprove).not.toHaveBeenCalled()
  })

  it('enables Approve when every flagged row is fixed', () => {
    const onApprove = vi.fn()
    const { rerender } = render(
      <BankStatementReview
        transactions={[
          {
            date: '',
            currency: '2020-08-11',
            withdrawal: 100,
            particulars: 'bad',
            account_type: 'HKD CURRENT',
          },
        ]}
        onApprove={onApprove}
        canApprove={true}
      />,
    )
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()

    rerender(
      <BankStatementReview
        transactions={[
          {
            date: '2020-08-11',
            currency: 'HKD',
            withdrawal: 100,
            balance: 900,
            particulars: 'fixed',
            account_type: 'HKD CURRENT',
          },
        ]}
        onApprove={onApprove}
        canApprove={true}
      />,
    )
    const approve = screen.getByRole('button', { name: 'Approve' })
    expect(approve).not.toBeDisabled()
    fireEvent.click(approve)
    expect(onApprove).toHaveBeenCalledTimes(1)
  })

  it('keeps Approve disabled when parent canApprove is false even if rows are clean', () => {
    render(
      <BankStatementReview
        transactions={[
          {
            date: '2020-08-11',
            currency: 'HKD',
            withdrawal: 100,
            balance: 900,
            particulars: 'ok',
            account_type: 'HKD CURRENT',
          },
        ]}
        onApprove={() => undefined}
        canApprove={false}
      />,
    )
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
  })
})
