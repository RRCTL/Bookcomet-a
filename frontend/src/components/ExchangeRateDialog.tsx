import { useMemo, useState } from 'react'
import {
  convertReceiptToCompany,
  formatCurrencyAmount,
  formatMoneyAmount,
  impliedRateFromPrinted,
  quantizeRate,
} from '../utils/fxCurrency'

type Props = {
  from: string
  to: string
  sampleReceiptAmount: number | null
  printedCompanyAmount?: number | null
  onApply: (rate: number, companyAmount?: number) => void
  onClose: () => void
}

export function ExchangeRateDialog({
  from,
  to,
  sampleReceiptAmount,
  printedCompanyAmount,
  onApply,
  onClose,
}: Props) {
  const [rateText, setRateText] = useState('')
  const rate = useMemo(() => {
    const n = parseFloat(rateText)
    return Number.isFinite(n) && n > 0 ? quantizeRate(n) : null
  }, [rateText])
  const preview = rate != null && sampleReceiptAmount != null
    ? convertReceiptToCompany(sampleReceiptAmount, rate)
    : null
  const printedRate =
    printedCompanyAmount != null && sampleReceiptAmount
      ? impliedRateFromPrinted(sampleReceiptAmount, printedCompanyAmount)
      : null

  return (
    <div className="settings-nested-overlay" role="presentation" onClick={onClose}>
      <div className="settings-form-modal" onClick={e => e.stopPropagation()} style={{ minWidth: 360 }}>
        <h4 className="settings-form-modal-title">Exchange rate</h4>
        <p className="settings-description">
          1 {from} ={' '}
          <input
            className="settings-input"
            style={{ width: 120, display: 'inline-block' }}
            inputMode="decimal"
            placeholder="rate"
            value={rateText}
            onChange={e => setRateText(e.target.value)}
          />{' '}
          {to}
        </p>
        {sampleReceiptAmount != null && (
          <p className="settings-company-hint">
            Preview: {formatCurrencyAmount(from, sampleReceiptAmount)} →{' '}
            {preview != null ? formatCurrencyAmount(to, preview) : '—'}
          </p>
        )}
        {printedCompanyAmount != null && (
          <p className="settings-company-hint">
            Printed on document: {formatCurrencyAmount(to, printedCompanyAmount)}
            {printedRate != null ? ` (implied ${printedRate})` : ''}
          </p>
        )}
        <div className="settings-form-modal-actions" style={{ marginTop: 16 }}>
          <button type="button" className="memory-btn" onClick={onClose}>Cancel</button>
          {printedCompanyAmount != null && (
            <button
              type="button"
              className="memory-btn"
              onClick={() => onApply(printedRate || 0, printedCompanyAmount)}
            >
              Keep printed {formatMoneyAmount(printedCompanyAmount)}
            </button>
          )}
          <button
            type="button"
            className="memory-btn primary"
            disabled={rate == null}
            onClick={() => rate != null && onApply(rate)}
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  )
}
