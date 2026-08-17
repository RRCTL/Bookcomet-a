import { useEffect, useState } from 'react'
import { CLOUD_AI_DATA_NOTICE } from '../constants/privacyNotices'
import { apiFetch } from '../services/api'
import './OnboardingWizard.css'

/** Client timeout: backend LLM read can be up to ~90s plus DB / rule memory / CoA — keep margin. */
const WIZARD_GENERATE_TIMEOUT_MS = 180_000

interface OnboardingWizardProps {
  onComplete: () => void
  onSkip: () => void
}

interface BankAccount {
  bank_name: string
  account_nickname: string
  currency: string
  opening_balance: string
  dr_cr: 'Dr' | 'Cr'
}

interface BankSettings {
  payment_method: 'bank' | 'cash' | 'both'
  accounts: BankAccount[]
  cash_account: BankAccount
  director_account: BankAccount
}

interface WizardData {
  company_name: string
  company_name_keywords: string
  industry: string
  accounting_basis: string
  fiscal_year_end: string
  key_clients: string
  key_vendors: string
  risk_rules: string
  glossary: string
  business_description: string
  bank_settings: BankSettings
}

export const STEPS = [
  {
    id: 'basics',
    title: 'Company Basics',
    subtitle: 'Company profile',
    icon: 'CO',
    description: 'This information will be used by the AI to understand your company. It is saved securely and used to improve classification accuracy.',
    showOptionalHint: false,
  },
  {
    id: 'clients',
    title: 'Key Clients',
    subtitle: 'Accounts receivable',
    icon: 'AR',
    description: 'Tell the AI about your important clients — their payment terms, risk level, and any special handling rules.',
    showOptionalHint: true,
    hint: 'Leave blank — the AI will identify your clients from AR invoices you upload and ask you to confirm.',
  },
  {
    id: 'bank',
    title: 'Bank Settings',
    subtitle: 'Bank and cash accounts',
    icon: 'BK',
    description: 'Set up your bank accounts and cash position. Entries will be automatically added to your Chart of Accounts.',
    showOptionalHint: true,
    hint: 'All balances are optional. Leave blank if zero or unknown — you can update them anytime in Chart of Accounts.',
  },
  {
    id: 'vendors',
    title: 'Key Vendors',
    subtitle: 'Accounts payable',
    icon: 'AP',
    description: 'Describe your key suppliers — contract terms, payment schedules, and any notes the AI should know.',
    showOptionalHint: true,
    hint: 'Leave blank — the AI will identify your suppliers from AP invoices you upload and ask you to confirm.',
  },
  {
    id: 'risk',
    title: 'Risk & Compliance',
    subtitle: 'Review thresholds',
    icon: 'RSK',
    description: 'Define thresholds and rules for flagging transactions that need manual review.',
    showOptionalHint: true,
    hint: 'Leave blank — you can add flagging rules anytime in Settings → Exclusion Rules.',
  },
  {
    id: 'glossary',
    title: 'Company Glossary',
    subtitle: 'Internal terms',
    icon: 'GLO',
    description: 'List internal abbreviations, project codes, or terms that appear in your documents.',
    showOptionalHint: true,
    hint: 'Leave blank — the AI will ask you about unfamiliar terms it encounters during document processing.',
  },
  {
    id: 'finalize',
    title: 'Generate & Finalize',
    subtitle: 'Review and generate',
    icon: 'GEN',
    description: 'Review your setup. The AI will generate your Company Manual, Rule Memory, and Chart of Accounts automatically.',
    showOptionalHint: false,
  },
]

const ACCOUNTING_BASIS_OPTIONS = ['Accrual', 'Cash']
const INDUSTRY_SUGGESTIONS = [
  'Retail', 'Trading', 'Manufacturing', 'Services', 'Construction',
  'Property', 'Finance', 'F&B', 'Logistics', 'Technology',
]
const CURRENCY_OPTIONS = ['HKD', 'USD', 'CNY', 'EUR', 'GBP', 'JPY']
const BANK_SUGGESTIONS = [
  'HSBC', 'Bank of China', 'Hang Seng Bank', 'Standard Chartered',
  'Citibank', 'DBS', 'OCBC', 'BEA', 'Wing Lung Bank', 'Fubon Bank',
]

const emptyBankAccount = (): BankAccount => ({
  bank_name: '', account_nickname: '', currency: 'HKD', opening_balance: '', dr_cr: 'Dr',
})

const defaultCashAccount = (): BankAccount => ({
  bank_name: '', account_nickname: 'Cash on Hand', currency: 'HKD', opening_balance: '', dr_cr: 'Dr',
})

const defaultDirectorAccount = (): BankAccount => ({
  bank_name: '', account_nickname: "Director's Current Account", currency: 'HKD', opening_balance: '', dr_cr: 'Dr',
})

function OptionalHint({ text }: { text: string }) {
  return (
    <div className="wizard-optional-hint">
      <span className="wizard-optional-hint-icon">ℹ</span>
      <span>{text} All fields are optional — the AI will update as it learns more about your business.</span>
    </div>
  )
}

export function OnboardingWizard({ onComplete, onSkip }: OnboardingWizardProps) {
  const [step, setStep] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [generatingElapsedSec, setGeneratingElapsedSec] = useState(0)
  const [error, setError] = useState('')
  const [data, setData] = useState<WizardData>({
    company_name: '',
    company_name_keywords: '',
    industry: '',
    accounting_basis: 'Accrual',
    fiscal_year_end: '',
    key_clients: '',
    key_vendors: '',
    risk_rules: '',
    glossary: '',
    business_description: '',
    bank_settings: {
      payment_method: 'bank',
      accounts: [emptyBankAccount()],
      cash_account: defaultCashAccount(),
      director_account: defaultDirectorAccount(),
    },
  })

  const currentStep = STEPS[step]
  const isLastStep = step === STEPS.length - 1
  const progressPct = Math.round((step / (STEPS.length - 1)) * 100)

  const update = (field: keyof WizardData, value: string | BankSettings) =>
    setData(prev => ({ ...prev, [field]: value }))

  const updateBank = (field: keyof BankSettings, value: BankSettings[keyof BankSettings]) =>
    setData(prev => ({ ...prev, bank_settings: { ...prev.bank_settings, [field]: value } }))

  const updateBankAccount = (idx: number, field: keyof BankAccount, value: string) =>
    setData(prev => {
      const accounts = prev.bank_settings.accounts.map((a, i) =>
        i === idx ? { ...a, [field]: value } : a
      )
      return { ...prev, bank_settings: { ...prev.bank_settings, accounts } }
    })

  const updateCashAccount = (field: keyof BankAccount, value: string) =>
    setData(prev => ({
      ...prev,
      bank_settings: {
        ...prev.bank_settings,
        cash_account: { ...prev.bank_settings.cash_account, [field]: value },
      },
    }))

  const updateDirectorAccount = (field: keyof BankAccount, value: string) =>
    setData(prev => ({
      ...prev,
      bank_settings: {
        ...prev.bank_settings,
        director_account: { ...prev.bank_settings.director_account, [field]: value },
      },
    }))

  const addBankAccount = () =>
    setData(prev => ({
      ...prev,
      bank_settings: {
        ...prev.bank_settings,
        accounts: [...prev.bank_settings.accounts, emptyBankAccount()],
      },
    }))

  const removeBankAccount = (idx: number) =>
    setData(prev => ({
      ...prev,
      bank_settings: {
        ...prev.bank_settings,
        accounts: prev.bank_settings.accounts.filter((_, i) => i !== idx),
      },
    }))

  const canProceed = () => {
    if (step === 0) return data.company_name.trim().length > 0
    return true
  }

  const handleNext = () => {
    if (step < STEPS.length - 1) setStep(s => s + 1)
  }

  const handleBack = () => {
    if (step > 0) setStep(s => s - 1)
  }

  useEffect(() => {
    if (!submitting) {
      setGeneratingElapsedSec(0)
      return
    }
    setGeneratingElapsedSec(0)
    const id = window.setInterval(() => {
      setGeneratingElapsedSec((s) => s + 1)
    }, 1000)
    return () => window.clearInterval(id)
  }, [submitting])

  const handleSubmit = async () => {
    setSubmitting(true)
    setError('')
    try {
      const res = await apiFetch('/company/manual/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...data,
          generate_rule_memory: true,
          generate_coa: true,
        }),
        timeoutMs: WIZARD_GENERATE_TIMEOUT_MS,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        const detail = err.detail
        if (res.status === 503 && detail === 'Request timed out') {
          throw new Error(
            `Request timed out after ${Math.round(WIZARD_GENERATE_TIMEOUT_MS / 1000)}s. Ensure the API is running and DEPLOY_API_KEY / LLM endpoint are reachable, then try again.`,
          )
        }
        const msg =
          typeof detail === 'string'
            ? detail
            : detail && typeof detail === 'object'
              ? JSON.stringify(detail)
              : `Error ${res.status}`
        throw new Error(msg)
      }
      onComplete()
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        setError(
          `Request timed out after ${Math.round(WIZARD_GENERATE_TIMEOUT_MS / 1000)}s. Ensure the API is running and DEPLOY_API_KEY / LLM endpoint are reachable, then try again.`,
        )
      } else {
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setSubmitting(false)
    }
  }

  const pm = data.bank_settings.payment_method
  const hasBankAccounts = pm === 'bank' || pm === 'both'
  const hasCash = pm === 'cash' || pm === 'both'
  const bankAccountCount = hasBankAccounts ? data.bank_settings.accounts.length : 0
  const cashCount = hasCash ? 1 : 0
  const totalCoaEntries = bankAccountCount + cashCount + 1 // +1 for director account always

  return (
    <div className="wizard-overlay">
      <div className="wizard-modal">
        {/* Header */}
        <div className="wizard-header">
          <div className="wizard-header-top">
            <div className="wizard-brand">Company Manual Setup</div>
            <button className="wizard-skip" onClick={onSkip} type="button">
              Skip for now →
            </button>
          </div>
          <div className="wizard-progress-bar">
            <div className="wizard-progress-fill" style={{ width: `${progressPct}%` }} />
          </div>
          <div className="wizard-progress-label">Step {step + 1} of {STEPS.length}</div>
        </div>

        {/* Step Content */}
        <div className="wizard-body">
          <div className="wizard-step-icon">{currentStep.icon}</div>
          <h2 className="wizard-step-title">
            {currentStep.title}
            <span className="wizard-step-subtitle">{currentStep.subtitle}</span>
          </h2>
          <p className="wizard-step-desc">{currentStep.description}</p>

          {currentStep.showOptionalHint && currentStep.hint && (
            <OptionalHint text={currentStep.hint} />
          )}

          {/* ── Step 0: Company Basics ── */}
          {step === 0 && (
            <div className="wizard-fields">
              <div className="wizard-field">
                <label>Company Name <span className="required">*</span></label>
                <input
                  type="text"
                  value={data.company_name}
                  onChange={e => update('company_name', e.target.value)}
                  placeholder="e.g. Example Trading Limited"
                  autoFocus
                />
              </div>
              <div className="wizard-field">
                <label>Company Name Aliases</label>
                <input
                  type="text"
                  value={data.company_name_keywords}
                  onChange={e => update('company_name_keywords', e.target.value)}
                  placeholder="e.g. Example Trading, Example Co (comma-separated)"
                />
                <div className="wizard-hint">Used by AI to identify your company in bank statements</div>
              </div>
              <div className="wizard-field">
                <label>Industry</label>
                <input
                  type="text"
                  value={data.industry}
                  onChange={e => update('industry', e.target.value)}
                  placeholder="e.g. Trading / Manufacturing / Services"
                  list="industry-suggestions"
                />
                <datalist id="industry-suggestions">
                  {INDUSTRY_SUGGESTIONS.map(s => <option key={s} value={s} />)}
                </datalist>
              </div>
              <div className="wizard-field-row">
                <div className="wizard-field">
                  <label>Accounting Basis</label>
                  <select value={data.accounting_basis} onChange={e => update('accounting_basis', e.target.value)}>
                    {ACCOUNTING_BASIS_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
                <div className="wizard-field">
                  <label>Fiscal Year End</label>
                  <input
                    type="text"
                    value={data.fiscal_year_end}
                    onChange={e => update('fiscal_year_end', e.target.value)}
                    placeholder="e.g. 03-31 or 12-31"
                  />
                </div>
              </div>
              <div className="wizard-field">
                <label>Brief Business Description</label>
                <textarea
                  value={data.business_description}
                  onChange={e => update('business_description', e.target.value)}
                  placeholder="e.g. Hong Kong SME trading company exporting to mainland China and Southeast Asia..."
                  rows={3}
                />
                <div className="wizard-hint">Used to generate Rule Memory and suggest a Chart of Accounts. Leave blank if unsure — AI will update as it learns from your documents.</div>
              </div>
            </div>
          )}

          {/* ── Step 1: Key Clients ── */}
          {step === 1 && (
            <div className="wizard-fields">
              <div className="wizard-field">
                <label>Key Clients</label>
                <textarea
                  value={data.key_clients}
                  onChange={e => update('key_clients', e.target.value)}
                  placeholder={`List your important clients and any special handling notes. For example:\n\n- ABC Trading Ltd: Standard 30-day payment terms. Reliable payer.\n- XYZ Corp: 60-day terms. Occasionally pays late — flag if overdue >75 days.\n- Government contracts: Require separate project codes. Always flag for partner review.`}
                  rows={10}
                />
              </div>
            </div>
          )}

          {/* ── Step 2: Bank Settings ── */}
          {step === 2 && (
            <div className="wizard-fields">
              {/* Payment method */}
              <div className="wizard-field">
                <label>How does the company receive and make payments?</label>
                <div className="wizard-radio-group">
                  {(['bank', 'cash', 'both'] as const).map(opt => (
                    <label key={opt} className="wizard-radio-label">
                      <input
                        type="radio"
                        name="payment_method"
                        value={opt}
                        checked={data.bank_settings.payment_method === opt}
                        onChange={() => updateBank('payment_method', opt)}
                      />
                      <span>
                        {opt === 'bank' ? 'Bank transfers only'
                          : opt === 'cash' ? 'Cash only'
                          : 'Both bank and cash'}
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              {/* ── Bank accounts ── */}
              {hasBankAccounts && (
                <>
                  <div className="wizard-bank-header">
                    <span className="wizard-bank-title">Bank Accounts</span>
                    <span className="wizard-bank-subtitle">
                      Each account will be added to your Chart of Accounts (1100-series)
                    </span>
                  </div>

                  {data.bank_settings.accounts.map((acct, idx) => (
                    <div key={idx} className="wizard-bank-card">
                      <div className="wizard-bank-card-header">
                        <span className="wizard-bank-card-num">Bank Account {idx + 1}</span>
                        {data.bank_settings.accounts.length > 1 && (
                          <button
                            type="button"
                            className="wizard-bank-remove"
                            onClick={() => removeBankAccount(idx)}
                          >×</button>
                        )}
                      </div>
                      <div className="wizard-field-row">
                        <div className="wizard-field">
                          <label>Bank Name</label>
                          <input
                            type="text"
                            value={acct.bank_name}
                            onChange={e => updateBankAccount(idx, 'bank_name', e.target.value)}
                            placeholder="e.g. HSBC"
                            list={`bank-suggestions-${idx}`}
                          />
                          <datalist id={`bank-suggestions-${idx}`}>
                            {BANK_SUGGESTIONS.map(b => <option key={b} value={b} />)}
                          </datalist>
                        </div>
                        <div className="wizard-field">
                          <label>Currency</label>
                          <select
                            value={acct.currency}
                            onChange={e => updateBankAccount(idx, 'currency', e.target.value)}
                          >
                            {CURRENCY_OPTIONS.map(c => <option key={c} value={c}>{c}</option>)}
                          </select>
                        </div>
                      </div>
                      <div className="wizard-field">
                        <label>Account Nickname</label>
                        <input
                          type="text"
                          value={acct.account_nickname}
                          onChange={e => updateBankAccount(idx, 'account_nickname', e.target.value)}
                          placeholder="e.g. Main HKD Current, USD Settlement"
                        />
                        <div className="wizard-hint">This becomes the account name in your Chart of Accounts</div>
                      </div>
                      <div className="wizard-field-row">
                        <div className="wizard-field">
                          <label>Opening Balance B/F</label>
                          <input
                            type="number"
                            step="0.01"
                            value={acct.opening_balance}
                            onChange={e => updateBankAccount(idx, 'opening_balance', e.target.value)}
                            placeholder="Leave blank if zero or unknown"
                          />
                        </div>
                        <div className="wizard-field" style={{ maxWidth: 130 }}>
                          <label>Dr / Cr</label>
                          <select
                            value={acct.dr_cr}
                            onChange={e => updateBankAccount(idx, 'dr_cr', e.target.value as 'Dr' | 'Cr')}
                          >
                            <option value="Dr">Dr (Asset)</option>
                            <option value="Cr">Cr (Overdraft)</option>
                          </select>
                        </div>
                      </div>
                    </div>
                  ))}

                  <button type="button" className="wizard-bank-add" onClick={addBankAccount}>
                    + Add Another Bank Account
                  </button>
                </>
              )}

              {/* ── Cash account ── */}
              {hasCash && (
                <>
                  <div className="wizard-bank-header">
                    <span className="wizard-bank-title">Cash on Hand</span>
                    <span className="wizard-bank-subtitle">
                      Will be added to Chart of Accounts (code 1010)
                    </span>
                  </div>

                  <div className="wizard-bank-card">
                    <div className="wizard-field-row">
                      <div className="wizard-field">
                        <label>Account Nickname</label>
                        <input
                          type="text"
                          value={data.bank_settings.cash_account.account_nickname}
                          onChange={e => updateCashAccount('account_nickname', e.target.value)}
                          placeholder="e.g. Cash on Hand, Petty Cash"
                        />
                      </div>
                      <div className="wizard-field">
                        <label>Currency</label>
                        <select
                          value={data.bank_settings.cash_account.currency}
                          onChange={e => updateCashAccount('currency', e.target.value)}
                        >
                          {CURRENCY_OPTIONS.map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </div>
                    </div>
                    <div className="wizard-field-row">
                      <div className="wizard-field">
                        <label>Opening Balance B/F</label>
                        <input
                          type="number"
                          step="0.01"
                          value={data.bank_settings.cash_account.opening_balance}
                          onChange={e => updateCashAccount('opening_balance', e.target.value)}
                          placeholder="Leave blank if zero or unknown"
                        />
                      </div>
                      <div className="wizard-field" style={{ maxWidth: 130 }}>
                        <label>Dr / Cr</label>
                        <select
                          value={data.bank_settings.cash_account.dr_cr}
                          onChange={e => updateCashAccount('dr_cr', e.target.value as 'Dr' | 'Cr')}
                        >
                          <option value="Dr">Dr (Asset)</option>
                          <option value="Cr">Cr (Rare)</option>
                        </select>
                      </div>
                    </div>
                  </div>
                </>
              )}

              {/* ── Director / Owner account — always shown ── */}
              <div className="wizard-bank-header">
                <span className="wizard-bank-title">Director / Owner Account <span className="wizard-bank-subtitle-inline">suspense account</span></span>
                <span className="wizard-bank-subtitle">
                  Tracks funds between company and owner. Will be added to Chart of Accounts (code 2100)
                </span>
              </div>

              <div className="wizard-bank-card">
                <div className="wizard-field">
                  <label>Account Nickname</label>
                  <input
                    type="text"
                    value={data.bank_settings.director_account.account_nickname}
                    onChange={e => updateDirectorAccount('account_nickname', e.target.value)}
                    placeholder="e.g. Director's Current Account"
                  />
                  <div className="wizard-hint">Common in HK SMEs — tracks owner injections, withdrawals, and loans to/from the company</div>
                </div>
                <div className="wizard-field-row">
                  <div className="wizard-field">
                    <label>Opening Balance B/F</label>
                    <input
                      type="number"
                      step="0.01"
                      value={data.bank_settings.director_account.opening_balance}
                      onChange={e => updateDirectorAccount('opening_balance', e.target.value)}
                      placeholder="Leave blank if zero or unknown"
                    />
                  </div>
                  <div className="wizard-field" style={{ maxWidth: 130 }}>
                    <label>Dr / Cr</label>
                    <select
                      value={data.bank_settings.director_account.dr_cr}
                      onChange={e => updateDirectorAccount('dr_cr', e.target.value as 'Dr' | 'Cr')}
                    >
                      <option value="Dr">Dr (Co. owes owner)</option>
                      <option value="Cr">Cr (Owner owes Co.)</option>
                    </select>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ── Step 3: Key Vendors ── */}
          {step === 3 && (
            <div className="wizard-fields">
              <div className="wizard-field">
                <label>Key Vendors / Suppliers</label>
                <textarea
                  value={data.key_vendors}
                  onChange={e => update('key_vendors', e.target.value)}
                  placeholder={`List your important suppliers and payment details. For example:\n\n- Mainland Supplier A: Monthly payment on 15th. Invoice always in RMB.\n- Office Supplies Co: Small recurring purchases. Auto-classify to Admin expense.\n- Freight forwarder: Charges vary — always verify amount before approving.`}
                  rows={10}
                />
              </div>
            </div>
          )}

          {/* ── Step 4: Risk Rules ── */}
          {step === 4 && (
            <div className="wizard-fields">
              <div className="wizard-field">
                <label>Risk & Compliance Rules</label>
                <textarea
                  value={data.risk_rules}
                  onChange={e => update('risk_rules', e.target.value)}
                  placeholder={`Define what should be flagged for human review. For example:\n\n- Any single transaction over HKD 50,000 requires partner approval.\n- Cash payments over HKD 5,000 must be flagged.\n- Any vendor not in the approved vendor list should be flagged.\n- Related-party transactions must always be reviewed.`}
                  rows={10}
                />
              </div>
            </div>
          )}

          {/* ── Step 5: Glossary ── */}
          {step === 5 && (
            <div className="wizard-fields">
              <div className="wizard-field">
                <label>Company Glossary & Internal Codes</label>
                <textarea
                  value={data.glossary}
                  onChange={e => update('glossary', e.target.value)}
                  placeholder={`List internal terms, abbreviations, and project codes. For example:\n\n- "EXT" = Example Trading (our company name abbreviation)\n- "Project A" = Project code PRJ-001, classify under Account 5100\n- "HQ Transfer" = Intercompany transfer to holding company, always flag\n- "Petty Cash" = Account 1010, handled by admin team`}
                  rows={10}
                />
              </div>
            </div>
          )}

          {/* ── Step 6: Finalize ── */}
          {step === 6 && (
            <div className="wizard-fields">
              <div className="wizard-summary">
                <div className="wizard-summary-row">
                  <span className="wizard-summary-label">Company</span>
                  <span className="wizard-summary-value">{data.company_name || '—'}</span>
                </div>
                <div className="wizard-summary-row">
                  <span className="wizard-summary-label">Industry</span>
                  <span className="wizard-summary-value">{data.industry || '—'}</span>
                </div>
                <div className="wizard-summary-row">
                  <span className="wizard-summary-label">Basis</span>
                  <span className="wizard-summary-value">{data.accounting_basis}</span>
                </div>
                <div className="wizard-summary-row">
                  <span className="wizard-summary-label">Fiscal Year End</span>
                  <span className="wizard-summary-value">{data.fiscal_year_end || '—'}</span>
                </div>
                <div className="wizard-summary-row">
                  <span className="wizard-summary-label">Key Clients</span>
                  <span className="wizard-summary-value">{data.key_clients.trim() ? '✓ Provided' : 'AI will learn from AR invoices'}</span>
                </div>
                <div className="wizard-summary-row">
                  <span className="wizard-summary-label">Payment</span>
                  <span className="wizard-summary-value">
                    {pm === 'cash' ? 'Cash only' : pm === 'bank' ? `${bankAccountCount} bank account${bankAccountCount !== 1 ? 's' : ''}` : `${bankAccountCount} bank + cash`}
                    {' '}&rarr; {totalCoaEntries} CoA entries
                  </span>
                </div>
                <div className="wizard-summary-row">
                  <span className="wizard-summary-label">Key Vendors</span>
                  <span className="wizard-summary-value">{data.key_vendors.trim() ? '✓ Provided' : 'AI will learn from AP invoices'}</span>
                </div>
                <div className="wizard-summary-row">
                  <span className="wizard-summary-label">Risk Rules</span>
                  <span className="wizard-summary-value">{data.risk_rules.trim() ? '✓ Provided' : '—'}</span>
                </div>
              </div>

              <div className="wizard-auto-notice">
                <div className="wizard-auto-notice-row">✓ Company Manual will be generated from your inputs</div>
                <div className="wizard-auto-notice-row">✓ Rule Memory for AR / AP / BANK / OTHER will be created</div>
                <div className="wizard-auto-notice-row">✓ Chart of Accounts with {totalCoaEntries} entr{totalCoaEntries !== 1 ? 'ies' : 'y'} will be set up</div>
                <div className="wizard-auto-notice-row wizard-auto-notice-muted">
                  Don't worry if some fields were left blank — the AI will fill in the gaps as it processes your documents.
                </div>
                <div className="wizard-auto-notice-row wizard-auto-notice-muted">
                  {CLOUD_AI_DATA_NOTICE}
                </div>
              </div>

              {error && <div className="wizard-error">{error}</div>}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="wizard-footer">
          {step > 0 && (
            <button type="button" className="wizard-btn secondary" onClick={handleBack} disabled={submitting}>
              ← Back
            </button>
          )}
          <div className="wizard-footer-right wizard-footer-generate">
            {!isLastStep ? (
              <button type="button" className="wizard-btn primary" onClick={handleNext} disabled={!canProceed()}>
                Next →
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="wizard-btn primary"
                  onClick={handleSubmit}
                  disabled={submitting}
                  aria-busy={submitting}
                >
                  {submitting
                    ? `Generating… (${generatingElapsedSec}s)`
                    : 'Generate Company Manual'}
                </button>
                {submitting && (
                  <p className="wizard-generating-hint">
                    Usually finishes within 1–3 minutes (AI manual + Rule Memory + CoA). If this exceeds{' '}
                    {Math.round(WIZARD_GENERATE_TIMEOUT_MS / 1000)}s, check the API terminal and browser Network tab.
                  </p>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
