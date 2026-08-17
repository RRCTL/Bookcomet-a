import { useState } from 'react'
import './ReportSetupCard.css'

export type ReportSetupCardData = {
  defaultDateFrom: string
  defaultDateTo: string
  defaultSuspenseCode: string
  suspenseOptions: Array<{ code: string; name: string }>
  defaultArControlCode: string
  defaultApControlCode: string
  defaultBankCode: string
  controlAccountOptions: Array<{ code: string; name: string }>
  isGenerated: boolean
}

type ReportSetupCardProps = {
  data: ReportSetupCardData
  onGenerate: (opts: {
    dateFrom: string
    dateTo: string
    suspenseCode: string
    arControlCode: string
    apControlCode: string
    bankCode: string
  }) => void
}

function AccountSelect({
  label,
  value,
  onChange,
  options,
  disabled,
  placeholder,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  options: Array<{ code: string; name: string }>
  disabled: boolean
  placeholder?: string
}) {
  return (
    <div className="report-setup-field">
      <label>{label}</label>
      {options.length > 0 ? (
        <select value={value} onChange={e => onChange(e.target.value)} disabled={disabled}>
          {options.map(opt => (
            <option key={opt.code} value={opt.code}>
              {opt.code} {opt.name}
            </option>
          ))}
        </select>
      ) : (
        <input
          type="text"
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder ?? 'e.g. 1100'}
          disabled={disabled}
        />
      )}
    </div>
  )
}

export function ReportSetupCard({ data, onGenerate }: ReportSetupCardProps) {
  const [dateFrom, setDateFrom]         = useState(data.defaultDateFrom)
  const [dateTo, setDateTo]             = useState(data.defaultDateTo)
  const [suspenseCode, setSuspenseCode] = useState(data.defaultSuspenseCode)
  const [arControlCode, setArControl]   = useState(data.defaultArControlCode)
  const [apControlCode, setApControl]   = useState(data.defaultApControlCode)
  const [bankCode, setBankCode]         = useState(data.defaultBankCode)
  const [submitted, setSubmitted]       = useState(data.isGenerated)

  const handleGenerate = () => {
    if (submitted) return
    setSubmitted(true)
    onGenerate({ dateFrom, dateTo, suspenseCode, arControlCode, apControlCode, bankCode })
  }

  return (
    <div className={`report-setup-card ${submitted ? 'is-generated' : ''}`}>
      <div className="report-setup-title">Report setup</div>

      <div className="report-setup-fields">
        <div className="report-setup-field">
          <label>Period start</label>
          <input
            type="date"
            value={dateFrom}
            onChange={e => setDateFrom(e.target.value)}
            disabled={submitted}
          />
        </div>
        <div className="report-setup-field">
          <label>Period end</label>
          <input
            type="date"
            value={dateTo}
            onChange={e => setDateTo(e.target.value)}
            disabled={submitted}
          />
        </div>

        <div className="report-setup-divider" />

        <AccountSelect
          label="AR control account (debit AR)"
          value={arControlCode}
          onChange={setArControl}
          options={data.controlAccountOptions}
          disabled={submitted}
          placeholder="e.g. 1100"
        />
        <AccountSelect
          label="AP control account (credit AP)"
          value={apControlCode}
          onChange={setApControl}
          options={data.controlAccountOptions}
          disabled={submitted}
          placeholder="e.g. 2100"
        />
        <AccountSelect
          label="Bank / cash account"
          value={bankCode}
          onChange={setBankCode}
          options={data.controlAccountOptions}
          disabled={submitted}
          placeholder="e.g. 1000"
        />

        <div className="report-setup-divider" />

        <AccountSelect
          label="Suspense account (uncoded transactions)"
          value={suspenseCode}
          onChange={setSuspenseCode}
          options={data.suspenseOptions}
          disabled={submitted}
          placeholder="e.g. 9999"
        />
      </div>

      <div className="report-setup-actions">
        {submitted ? (
          <div className="report-setup-done">Report generated</div>
        ) : (
          <button className="report-setup-btn-generate" onClick={handleGenerate}>
            Generate report
          </button>
        )}
      </div>
    </div>
  )
}
