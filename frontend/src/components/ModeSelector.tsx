import { useState, useRef, useEffect } from 'react'
import './ModeSelector.css'

export type ProcessingMode = 'AR' | 'AP' | 'BANK' | 'RECON' | 'REPORT' | 'OTHER'

type ModeSelectorProps = {
  selectedMode: ProcessingMode
  onChange: (mode: ProcessingMode) => void
  disabled?: boolean
  compact?: boolean
}

export const MODE_META: Record<ProcessingMode, { label: string; shortLabel: string; description: string }> = {
  AR: { label: 'AR - Receivables', shortLabel: 'AR', description: 'Invoices, bank-in slips, cheques received' },
  AP: { label: 'AP - Payables', shortLabel: 'AP', description: 'Invoices, payment slips, cheques issued' },
  BANK: { label: 'BANK - Bank Statement', shortLabel: 'BANK', description: 'OCR bank statements into bank sheets' },
  RECON: { label: 'RECON - Reconciliation', shortLabel: 'RECON', description: 'Match bank statements with ledger records' },
  REPORT: { label: 'REPORT - Financial Statements', shortLabel: 'RPT', description: 'Trial balance, P&L, and balance sheet' },
  OTHER: { label: 'Other', shortLabel: 'Other', description: 'Loans, fixed assets, depreciation tracking' },
}

/** Primary OCR modes in the header picker (parity: AR / AP / BANK / OTHER). */
export const DROPDOWN_MODES: ProcessingMode[] = ['AR', 'AP', 'BANK', 'OTHER']

/** User-facing short label for a processing mode code (e.g. OTHER → Other). */
export function processingModeLabel(mode: string): string {
  return MODE_META[mode as ProcessingMode]?.shortLabel ?? mode
}

export function ModeSelector({ selectedMode, onChange, disabled, compact }: ModeSelectorProps) {
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  const selectedModeObj = MODE_META[selectedMode]

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  const handleSelect = (mode: ProcessingMode) => {
    onChange(mode)
    setIsOpen(false)
  }

  if (compact) {
    return (
      <div className="mode-segment-control">
        {DROPDOWN_MODES.map(mode => (
          <button
            key={mode}
            className={`mode-segment-btn ${selectedMode === mode ? 'mode-segment-btn--active' : ''}`}
            onClick={() => !disabled && onChange(mode)}
            disabled={disabled}
          >
            {MODE_META[mode].shortLabel}
          </button>
        ))}
      </div>
    )
  }

  return (
    <div className="mode-selector-container" ref={dropdownRef}>
      <button
        className={`mode-selector-trigger ${isOpen ? 'open' : ''}`}
        onClick={() => !disabled && setIsOpen(!isOpen)}
        disabled={disabled}
        data-mode={selectedMode}
      >
        <span className="mode-label">{selectedModeObj?.label || 'Select Mode'}</span>
        <svg className="chevron-icon" width="12" height="8" viewBox="0 0 12 8" fill="none">
          <path d="M1 1.5L6 6.5L11 1.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>

      {isOpen && !disabled && (
        <div className="mode-dropdown-menu">
          {DROPDOWN_MODES.map(mode => (
            <div
              key={mode}
              className={`mode-option ${selectedMode === mode ? 'selected' : ''}`}
              onClick={() => handleSelect(mode)}
            >
              <div className="mode-option-content">
                <div className="mode-option-label">{MODE_META[mode].label}</div>
                <div className="mode-option-description">{MODE_META[mode].description}</div>
              </div>
              {selectedMode === mode && (
                <svg className="check-icon" width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M13.5 4L6 11.5L2.5 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
