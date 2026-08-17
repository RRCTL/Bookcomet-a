import {
  AP_GUESS_MODE_SLASH,
  AP_RECEIPT_OPTIONS_ORDER,
  AP_RECEIPT_SLASH_LABEL,
  AP_TABLE_AP_SLASH,
  AP_TABLE_DEFAULT_SLASH,
  AP_TABLE_OPTIONS_ORDER,
  type ApVlmReceiptSignal,
  type ApVlmTablePreset,
} from './apComposerOptions'

import './ApComposerModalPickLists.css'

export function ApModalReceiptPickList({
  selected,
  onSelect,
}: {
  selected: ApVlmReceiptSignal | null
  onSelect: (v: ApVlmReceiptSignal) => void
}) {
  return (
    <div className="ap-modal-pick-list" role="list">
      {AP_RECEIPT_OPTIONS_ORDER.map(signal => {
        const label = signal === 'guess' ? AP_GUESS_MODE_SLASH : AP_RECEIPT_SLASH_LABEL[signal]
        const active = selected === signal
        return (
          <button
            key={signal}
            type="button"
            role="listitem"
            className={`ap-modal-pick-row${active ? ' ap-modal-pick-row--active' : ''}`}
            onClick={() => onSelect(signal)}
          >
            <span className="ap-modal-pick-row-label">{label}</span>
            {active && (
              <svg className="ap-modal-pick-check" width={16} height={16} viewBox="0 0 16 16" fill="none" aria-hidden>
                <path d="M13.5 4L6 11.5L2.5 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
          </button>
        )
      })}
    </div>
  )
}

export function ApModalTablePickList({
  selected,
  onSelect,
}: {
  selected: ApVlmTablePreset | null
  onSelect: (v: ApVlmTablePreset) => void
}) {
  return (
    <div className="ap-modal-pick-list" role="list">
      {AP_TABLE_OPTIONS_ORDER.map(preset => {
        const label = preset === 'ap_table' ? AP_TABLE_AP_SLASH : AP_TABLE_DEFAULT_SLASH
        const active = selected === preset
        return (
          <button
            key={preset}
            type="button"
            role="listitem"
            className={`ap-modal-pick-row${active ? ' ap-modal-pick-row--active' : ''}`}
            onClick={() => onSelect(preset)}
          >
            <span className="ap-modal-pick-row-label">{label}</span>
            {active && (
              <svg className="ap-modal-pick-check" width={16} height={16} viewBox="0 0 16 16" fill="none" aria-hidden>
                <path d="M13.5 4L6 11.5L2.5 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
          </button>
        )
      })}
    </div>
  )
}
