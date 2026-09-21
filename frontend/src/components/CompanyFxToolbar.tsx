import { FX_CURRENCY_OPTIONS } from '../utils/fxCurrency'

type Props = {
  reportingCurrency: string
  tableCurrency: string
  onTableCurrencyChange: (next: string) => void
  hideReceiptCurrency: boolean
  onToggleReceiptCurrency: () => void
  fxBlocked: boolean
}

export function CompanyFxToolbar({
  reportingCurrency,
  tableCurrency,
  onTableCurrencyChange,
  hideReceiptCurrency,
  onToggleReceiptCurrency,
  fxBlocked,
}: Props) {
  const locked = Boolean(reportingCurrency)
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', padding: '8px 12px' }}>
      <label style={{ fontSize: 12, color: '#64748b' }}>
        Company currency{' '}
        {locked ? (
          <strong>{reportingCurrency}</strong>
        ) : (
          <select
            value={tableCurrency}
            onChange={e => onTableCurrencyChange(e.target.value)}
            style={{ marginLeft: 6 }}
          >
            <option value="">Select</option>
            {FX_CURRENCY_OPTIONS.map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        )}
      </label>
      <button type="button" className="erp-btn" onClick={onToggleReceiptCurrency}>
        {hideReceiptCurrency ? 'Show receipt currency' : 'Hide receipt currency'}
      </button>
      {fxBlocked && (
        <span style={{ fontSize: 12, color: '#b45309' }}>
          Set company amounts for every row before approve.
        </span>
      )}
    </div>
  )
}
