import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import {
  DROPDOWN_MODES,
  MODE_META,
  type ProcessingMode,
} from '../../components/ModeSelector'
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
import './ComposerWorkspaceHub.css'

type ApSegmentOpen = null | 'receipt' | 'table'

function apReceiptShortLabel(signal: ApVlmReceiptSignal): string {
  switch (signal) {
    case 'guess':
      return 'Guess'
    case 'single_per_page':
      return 'Single / page'
    case 'multi_per_page':
      return 'Multi / page'
    case 'single_span_pages':
      return 'Span pages'
    default:
      return 'Receipt'
  }
}

function apTableShortLabel(preset: ApVlmTablePreset): string {
  return preset === 'ap_table' ? 'AP table' : 'Default'
}

function IconReceiptOutline({ muted }: { muted: boolean }) {
  const stroke = muted ? '#9ca3af' : '#b45309'
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M8 6h12v14H8V6zM8 6V4a2 2 0 012-2h8a2 2 0 012 2v2"
        stroke={stroke}
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M10 11h8M10 14h6" stroke={stroke} strokeWidth={1.6} strokeLinecap="round" />
    </svg>
  )
}

function IconTableOutline({ muted }: { muted: boolean }) {
  const stroke = muted ? '#9ca3af' : '#b45309'
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M5 5h14v14H5V5zM5 11h14M12 5v14"
        stroke={stroke}
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export type ComposerWorkspaceHubProps = {
  disabled?: boolean
  processingMode: ProcessingMode
  onSelectMode: (mode: ProcessingMode) => void
  apReceiptSignal: ApVlmReceiptSignal | null
  apTablePreset: ApVlmTablePreset | null
  onApReceiptSignal: (value: ApVlmReceiptSignal | null) => void
  onApTablePreset: (value: ApVlmTablePreset | null) => void
  onBankInsertCashTable: () => void
  /** Full composer bar (hub + chips + input + actions); mousedown inside does not count as "outside hub". */
  hubDismissBoundsRef?: RefObject<HTMLElement | null>
}

function norm(s: string) {
  return s.trim().toLowerCase()
}

function matches(q: string, ...parts: string[]) {
  if (!q) return true
  const n = norm(q)
  return parts.some(p => norm(p).includes(n))
}

export function ComposerWorkspaceHub({
  disabled,
  processingMode,
  onSelectMode,
  apReceiptSignal,
  apTablePreset,
  onApReceiptSignal,
  onApTablePreset,
  onBankInsertCashTable,
  hubDismissBoundsRef,
}: ComposerWorkspaceHubProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const plusButtonRef = useRef<HTMLButtonElement>(null)

  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [apSegmentOpen, setApSegmentOpen] = useState<ApSegmentOpen>(null)

  const popoverId = useId()
  const segmentMenuId = useId()

  const closeHub = useCallback(() => {
    setOpen(false)
    setQuery('')
  }, [])

  const closeApSegmentPopover = useCallback(() => {
    setApSegmentOpen(null)
  }, [])

  const openHub = useCallback(() => {
    setApSegmentOpen(null)
    setOpen(true)
    setQuery('')
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    const t = window.setTimeout(() => searchRef.current?.focus(), 0)
    return () => window.clearTimeout(t)
  }, [open])

  useEffect(() => {
    if (!open && !apSegmentOpen) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        closeApSegmentPopover()
        closeHub()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, apSegmentOpen, closeHub, closeApSegmentPopover])

  useEffect(() => {
    if (!open) return
    const onMouseDown = (e: MouseEvent) => {
      const t = e.target as Node
      const bounds = hubDismissBoundsRef?.current
      if (bounds?.contains(t)) return
      const el = rootRef.current
      if (el && !el.contains(t)) {
        closeHub()
      }
    }
    const timer = window.setTimeout(() => document.addEventListener('mousedown', onMouseDown), 0)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('mousedown', onMouseDown)
    }
  }, [open, closeHub, hubDismissBoundsRef])

  useEffect(() => {
    if (!apSegmentOpen || open) return
    const onMouseDown = (e: MouseEvent) => {
      const t = e.target as Node
      const bounds = hubDismissBoundsRef?.current
      if (bounds?.contains(t)) return
      const el = rootRef.current
      if (el && !el.contains(t)) closeApSegmentPopover()
    }
    const timer = window.setTimeout(() => document.addEventListener('mousedown', onMouseDown), 0)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('mousedown', onMouseDown)
    }
  }, [apSegmentOpen, open, closeApSegmentPopover, hubDismissBoundsRef])

  useEffect(() => {
    if (processingMode !== 'AP') setApSegmentOpen(null)
  }, [processingMode])

  const wasOpenRef = useRef(false)
  useEffect(() => {
    if (wasOpenRef.current && !open) {
      plusButtonRef.current?.focus()
    }
    wasOpenRef.current = open
  }, [open])

  const selectMode = (mode: ProcessingMode) => {
    onSelectMode(mode)
    closeApSegmentPopover()
    closeHub()
  }

  const selectReceipt = (v: ApVlmReceiptSignal) => {
    onApReceiptSignal(apReceiptSignal === v ? null : v)
    closeApSegmentPopover()
    closeHub()
  }

  const selectTable = (v: ApVlmTablePreset) => {
    onApTablePreset(apTablePreset === v ? null : v)
    closeApSegmentPopover()
    closeHub()
  }

  const toggleApSegment = (which: Exclude<ApSegmentOpen, null>) => {
    if (disabled) return
    if (open) closeHub()
    setApSegmentOpen(prev => (prev === which ? null : which))
  }

  const handleBankInsert = () => {
    onBankInsertCashTable()
    closeApSegmentPopover()
    closeHub()
  }

  const filteredModes = DROPDOWN_MODES.filter(mode => {
    const m = MODE_META[mode]
    return matches(query, m.label, m.shortLabel, m.description)
  })

  const showBankRow =
    processingMode === 'BANK' && matches(query, 'Create cash table', 'cash', 'table')

  const showModesSection = filteredModes.length > 0
  const showOptionsSection = showBankRow

  const receiptSegmentRows = AP_RECEIPT_OPTIONS_ORDER.map(signal => ({
    signal,
    label: signal === 'guess' ? AP_GUESS_MODE_SLASH : AP_RECEIPT_SLASH_LABEL[signal],
  }))

  const tableSegmentRows = AP_TABLE_OPTIONS_ORDER.map(preset => ({
    preset,
    label: preset === 'ap_table' ? AP_TABLE_AP_SLASH : AP_TABLE_DEFAULT_SLASH,
  }))

  const apWrapperClass =
    processingMode === 'AP'
      ? 'composer-plus-wrapper composer-plus-wrapper-ap'
      : 'composer-plus-wrapper'

  return (
    <div className={`composer-hub ${apWrapperClass}`} ref={rootRef}>
      <div className="composer-hub-triggers">
        {processingMode === 'AP' ? (
          <div className="composer-ap-segment-track" role="group" aria-label="AP extraction options">
            <button
              type="button"
              className={
                'composer-ap-segment-slot' +
                (apReceiptSignal !== null ? ' composer-ap-segment-slot--active' : '')
              }
              disabled={disabled}
              aria-haspopup="menu"
              aria-expanded={apSegmentOpen === 'receipt'}
              aria-controls={segmentMenuId}
              onClick={() => toggleApSegment('receipt')}
            >
              {apReceiptSignal !== null ? (
                <span className="composer-ap-segment-pill">
                  <IconReceiptOutline muted={false} />
                  <span className="composer-ap-segment-pill-label">{apReceiptShortLabel(apReceiptSignal)}</span>
                </span>
              ) : (
                <>
                  <IconReceiptOutline muted />
                  <span className="composer-ap-segment-muted-label">Receipt layout</span>
                </>
              )}
            </button>
            <button
              type="button"
              className={
                'composer-ap-segment-slot' +
                (apTablePreset !== null ? ' composer-ap-segment-slot--active' : '')
              }
              disabled={disabled}
              aria-haspopup="menu"
              aria-expanded={apSegmentOpen === 'table'}
              aria-controls={segmentMenuId}
              onClick={() => toggleApSegment('table')}
            >
              {apTablePreset !== null ? (
                <span className="composer-ap-segment-pill">
                  <IconTableOutline muted={false} />
                  <span className="composer-ap-segment-pill-label">{apTableShortLabel(apTablePreset)}</span>
                </span>
              ) : (
                <>
                  <IconTableOutline muted />
                  <span className="composer-ap-segment-muted-label">Table style</span>
                </>
              )}
            </button>
          </div>
        ) : (
          <button
            type="button"
            ref={plusButtonRef}
            className="composer-icon-btn composer-plus-btn"
            disabled={disabled}
            title="Insert option"
            aria-label="Open composer options"
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-controls={popoverId}
            onClick={() => {
              if (disabled) return
              if (open) closeHub()
              else openHub()
            }}
          >
            +
          </button>
        )}
      </div>

      {apSegmentOpen && !disabled && (
        <div
          id={segmentMenuId}
          className="composer-ap-segment-dropdown"
          role="menu"
          aria-label={apSegmentOpen === 'receipt' ? 'Receipt layout options' : 'Table style options'}
        >
          {apSegmentOpen === 'receipt'
            ? receiptSegmentRows.map(({ signal, label }) => {
                const picked = apReceiptSignal === signal
                return (
                  <button
                    key={signal}
                    type="button"
                    role="menuitem"
                    className={
                      'composer-ap-segment-dropdown-item' +
                      (picked ? ' composer-ap-segment-dropdown-item--picked' : '')
                    }
                    onClick={() => selectReceipt(signal)}
                  >
                    <span className="composer-ap-segment-dropdown-item-label">{label}</span>
                  </button>
                )
              })
            : tableSegmentRows.map(({ preset, label }) => {
                const picked = apTablePreset === preset
                return (
                  <button
                    key={preset}
                    type="button"
                    role="menuitem"
                    className={
                      'composer-ap-segment-dropdown-item' +
                      (picked ? ' composer-ap-segment-dropdown-item--picked' : '')
                    }
                    onClick={() => selectTable(preset)}
                  >
                    <span className="composer-ap-segment-dropdown-item-label">{label}</span>
                  </button>
                )
              })}
        </div>
      )}

      {open && !disabled && (
        <div
          id={popoverId}
          className="composer-hub-popover"
          role="dialog"
          aria-label="Composer options"
        >
          <div className="composer-hub-search">
            <input
              ref={searchRef}
              type="search"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Add options, modes..."
              aria-label="Filter composer options"
              autoComplete="off"
              spellCheck={false}
            />
          </div>

          <div className="composer-hub-scroll">
            {showModesSection && (
              <>
                <div className="composer-hub-section-label">Modes</div>
                {filteredModes.map(mode => {
                  const m = MODE_META[mode]
                  const active = processingMode === mode
                  return (
                    <button
                      key={mode}
                      type="button"
                      className={
                        'composer-hub-row' +
                        (active ? ' composer-hub-row--active' : '')
                      }
                      onClick={() => selectMode(mode)}
                    >
                      <div className="composer-hub-row-main">
                        <div className="composer-hub-row-label">{m.label}</div>
                        <div className="composer-hub-row-desc">{m.description}</div>
                      </div>
                      {active && (
                        <svg
                          className="composer-hub-check"
                          width={16}
                          height={16}
                          viewBox="0 0 16 16"
                          fill="none"
                          aria-hidden
                        >
                          <path
                            d="M13.5 4L6 11.5L2.5 8"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      )}
                    </button>
                  )
                })}
              </>
            )}

            {showModesSection && showOptionsSection && (
              <div className="composer-hub-divider" />
            )}

            {showOptionsSection && (
              <>
                <div className="composer-hub-section-label">Composer options</div>
                {showBankRow && (
                  <button
                    type="button"
                    className="composer-hub-row"
                    onClick={handleBankInsert}
                  >
                    <div className="composer-hub-row-main">
                      <div className="composer-hub-row-label">Create cash table</div>
                      <div className="composer-hub-row-desc">
                        Insert the cash records template message
                      </div>
                    </div>
                  </button>
                )}
              </>
            )}

            {!showModesSection && !showOptionsSection && (
              <div className="composer-hub-section-label" style={{ padding: '12px' }}>
                No matches
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
