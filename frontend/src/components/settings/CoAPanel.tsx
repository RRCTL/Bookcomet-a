import { Fragment } from 'react'
import { useSettings } from './SettingsProvider'

export function CoAPanel() {
  const s = useSettings() as Record<string, any>
  const {
    coaActiveMode, setCoaActiveMode, setCoaEditingCode, setCoaError,
    chartOfAccounts, coaEditingCode, coaEditDraft, setCoaEditDraft,
    coaNewDraft, setCoaNewDraft, coaAddingNew, setCoaAddingNew,
    coaError, coaTxnPanel, setCoaTxnPanel, coaBFDrafts, setCoaBFDrafts,
    coaBFSaving, coaReferencedCodes, allTransactions,
    handleCoaStartEdit, handleCoaSaveEdit, handleCoaDelete, handleCoaAddNew, handleBFBlur,
    reloadCoA,
  } = s

  return (
          <div className="settings-section">
            <h3>Chart of Accounts</h3>
            <p className="settings-description">
              Manage AR / AP / BANK account codes. AI classification uses these accounts. Built-in codes cannot be deleted, but you can edit names.
            </p>

            {/* Mode tabs */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              {(['AR', 'AP', 'BANK'] as const).map(m => (
                <button
                  key={m}
                  type="button"
                  onClick={() => { setCoaActiveMode(m); setCoaEditingCode(null); setCoaError('') }}
                  style={{
                    padding: '4px 14px', borderRadius: 14, border: 'none', cursor: 'pointer', fontSize: 12,
                    fontWeight: coaActiveMode === m ? 700 : 400,
                    background: coaActiveMode === m ? '#111827' : '#f3f4f6',
                    color: coaActiveMode === m ? '#fff' : '#374151',
                  }}
                >
                  {m} ({chartOfAccounts[m].length})
                </button>
              ))}
            </div>

            {coaError && (
              <div style={{ background: '#fef2f2', color: '#b91c1c', padding: '6px 10px', borderRadius: 6, fontSize: 12, marginBottom: 8 }}>
                {coaError}
              </div>
            )}

            {/* CoA table */}
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, background: '#fff', borderRadius: 6, overflow: 'hidden', boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
              <thead>
                <tr style={{ background: '#f9fafb', borderBottom: '2px solid #e5e7eb' }}>
                  <th style={{ padding: '7px 10px', textAlign: 'left', fontWeight: 600, color: '#374151' }}>Code</th>
                  <th style={{ padding: '7px 10px', textAlign: 'left', fontWeight: 600, color: '#374151' }}>English name</th>
                  <th style={{ padding: '7px 10px', textAlign: 'left', fontWeight: 600, color: '#374151' }}>Chinese name</th>
                  <th style={{ padding: '7px 10px', textAlign: 'left', fontWeight: 600, color: '#374151' }}>Type</th>
                  <th style={{ padding: '7px 10px', textAlign: 'left', fontWeight: 600, color: '#374151' }}>Modes</th>
                  <th style={{ padding: '7px 10px', textAlign: 'right', fontWeight: 600, color: '#374151' }}>Opening B/F</th>
                  <th style={{ padding: '7px 10px', textAlign: 'right', fontWeight: 600, color: '#374151' }}>YTD (HKD)</th>
                  <th style={{ padding: '7px 10px', textAlign: 'center', fontWeight: 600, color: '#374151' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {chartOfAccounts[coaActiveMode].map(item => {
                  const isEditing = coaEditingCode === item.code
                  const isReferenced = coaReferencedCodes.has(item.code)
                  const txnsForCode = allTransactions.filter(t => t.account_code === item.code)
                  const accTotal = txnsForCode.reduce((s, t) => s + (t.amount ?? 0), 0)
                  const isFinPos = /^[123]/.test(item.code)
                  const fmtCurrency = (n: number) => n.toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                  return (
                    <Fragment key={item.code}>
                      <tr style={{ borderBottom: '1px solid #f0f0f0' }}>
                        <td style={{ padding: '6px 10px', fontFamily: 'monospace', fontWeight: 600, color: '#2563eb' }}>
                          <button
                            type="button"
                            onClick={() => setCoaTxnPanel(coaTxnPanel === item.code ? null : item.code)}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#2563eb', fontWeight: 700, fontFamily: 'monospace', fontSize: 12, padding: 0 }}
                            title="View related transactions"
                          >
                            {item.code} {coaTxnPanel === item.code ? '▲' : '▼'}
                          </button>
                        </td>
                        {isEditing ? (
                          <>
                            <td style={{ padding: '4px 6px' }}>
                              <input className="settings-input" value={coaEditDraft.name_en} onChange={e => setCoaEditDraft(d => ({ ...d, name_en: e.target.value }))} style={{ width: '100%' }} />
                            </td>
                            <td style={{ padding: '4px 6px' }}>
                              <input className="settings-input" value={coaEditDraft.name_zh} onChange={e => setCoaEditDraft(d => ({ ...d, name_zh: e.target.value }))} style={{ width: '100%' }} />
                            </td>
                            <td style={{ padding: '4px 6px' }}>
                              <select className="settings-input" value={coaEditDraft.category_type} onChange={e => setCoaEditDraft(d => ({ ...d, category_type: e.target.value }))} style={{ width: '100%' }}>
                                {['revenue', 'expense', 'asset', 'liability', 'equity', 'other_income', 'bank_fee', 'interest_paid', 'cogs'].map(t => (
                                  <option key={t} value={t}>{t}</option>
                                ))}
                              </select>
                            </td>
                            <td style={{ padding: '4px 6px' }}>
                              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                {(['AR', 'AP', 'BANK'] as const).map(m => (
                                  <label key={m} style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 2 }}>
                                    <input
                                      type="checkbox"
                                      checked={coaEditDraft.allowed_modes.includes(m)}
                                      onChange={e => setCoaEditDraft(d => ({
                                        ...d,
                                        allowed_modes: e.target.checked ? [...d.allowed_modes, m] : d.allowed_modes.filter(x => x !== m)
                                      }))}
                                    /> {m}
                                  </label>
                                ))}
                              </div>
                            </td>
                            {/* 期初餘額 B/F — editable for 1/2/3xxx, read-only otherwise */}
                            <td style={{ padding: '4px 6px', textAlign: 'right' }}>
                              {isFinPos ? (
                                <div style={{ display: 'flex', gap: 4, alignItems: 'center', justifyContent: 'flex-end' }}>
                                  <input
                                    className="settings-input"
                                    type="number"
                                    step="0.01"
                                    placeholder="0.00"
                                    value={coaEditDraft.opening_balance}
                                    onChange={e => setCoaEditDraft(d => ({ ...d, opening_balance: e.target.value }))}
                                    style={{ width: 90, textAlign: 'right' }}
                                  />
                                  <select
                                    className="settings-input"
                                    value={coaEditDraft.opening_balance_dr_cr}
                                    onChange={e => setCoaEditDraft(d => ({ ...d, opening_balance_dr_cr: e.target.value }))}
                                    style={{ width: 52 }}
                                  >
                                    <option value="Dr">Dr</option>
                                    <option value="Cr">Cr</option>
                                  </select>
                                </div>
                              ) : (
                                <span style={{ fontSize: 11, color: '#bbb' }}>—</span>
                              )}
                            </td>
                            {/* 累計金額 — read-only in edit mode too */}
                            <td style={{ padding: '4px 6px', textAlign: 'right', color: accTotal !== 0 ? '#2563eb' : '#9ca3af', fontSize: 11 }}>
                              {accTotal !== 0 ? fmtCurrency(accTotal) : '—'}
                            </td>
                            <td style={{ padding: '4px 6px', textAlign: 'center', whiteSpace: 'nowrap' }}>
                              <button type="button" onClick={handleCoaSaveEdit} style={{ fontSize: 11, padding: '3px 8px', marginRight: 4, background: '#111827', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer' }}>Save</button>
                              <button type="button" onClick={() => { setCoaEditingCode(null); setCoaError('') }} style={{ fontSize: 11, padding: '3px 8px', background: '#f3f4f6', border: '1px solid #e5e7eb', borderRadius: 6, cursor: 'pointer' }}>Cancel</button>
                            </td>
                          </>
                        ) : (
                          <>
                            <td style={{ padding: '6px 10px', color: '#333' }}>{item.name_en}</td>
                            <td style={{ padding: '6px 10px', color: '#555' }}>{item.name_zh}</td>
                            <td style={{ padding: '6px 10px', color: '#777', fontSize: 11 }}>{item.category_type}</td>
                            <td style={{ padding: '6px 10px' }}>
                              <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                                {item.allowed_modes.map(m => (
                                  <span key={m} style={{ fontSize: 10, padding: '1px 5px', borderRadius: 8, background: '#eff6ff', color: '#2563eb', fontWeight: 600 }}>{m}</span>
                                ))}
                              </div>
                            </td>
                            {/* 期初餘額 B/F — always editable for 1/2/3xxx, auto-saves on blur */}
                            <td style={{ padding: '4px 6px', whiteSpace: 'nowrap' }}>
                              {isFinPos ? (
                                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                                  <input
                                    type="number"
                                    min={0}
                                    step="0.01"
                                    placeholder="0.00"
                                    style={{ width: 84, fontSize: 11, padding: '2px 5px', border: '1px solid #d1d5db', borderRadius: 4, textAlign: 'right', background: coaBFSaving[item.code] ? '#f9fafb' : '#fff' }}
                                    value={coaBFDrafts[item.code]?.amount ?? (item.opening_balance != null ? String(item.opening_balance) : '')}
                                    onChange={e => setCoaBFDrafts(d => ({ ...d, [item.code]: { amount: e.target.value, drCr: d[item.code]?.drCr ?? item.opening_balance_dr_cr ?? 'Dr' } }))}
                                    onBlur={() => handleBFBlur(item)}
                                  />
                                  <select
                                    style={{ fontSize: 11, padding: '2px 4px', border: '1px solid #d1d5db', borderRadius: 4 }}
                                    value={coaBFDrafts[item.code]?.drCr ?? item.opening_balance_dr_cr ?? 'Dr'}
                                    onChange={e => setCoaBFDrafts(d => ({ ...d, [item.code]: { amount: d[item.code]?.amount ?? (item.opening_balance != null ? String(item.opening_balance) : ''), drCr: e.target.value } }))}
                                    onBlur={() => handleBFBlur(item)}
                                  >
                                    <option value="Dr">Dr</option>
                                    <option value="Cr">Cr</option>
                                  </select>
                                  {coaBFSaving[item.code] && <span style={{ fontSize: 10, color: '#9ca3af' }}>…</span>}
                                </span>
                              ) : (
                                <span style={{ color: '#bbb', fontSize: 11 }}>—</span>
                              )}
                            </td>
                            {/* 累計金額 */}
                            <td style={{ padding: '6px 10px', textAlign: 'right', fontFamily: 'monospace', fontSize: 12,
                              color: accTotal !== 0 ? '#2563eb' : '#9ca3af' }}>
                              {accTotal !== 0 ? fmtCurrency(accTotal) : '—'}
                            </td>
                            <td style={{ padding: '6px 10px', textAlign: 'center', whiteSpace: 'nowrap' }}>
                              <button type="button" onClick={() => handleCoaStartEdit(item)} style={{ fontSize: 11, padding: '3px 8px', marginRight: 4, background: '#f3f4f6', border: '1px solid #e5e7eb', borderRadius: 6, cursor: 'pointer' }}>Edit</button>
                              <button
                                type="button"
                                onClick={() => !item.is_default && !isReferenced ? handleCoaDelete(item.code) : setCoaError(item.is_default ? `Built-in account '${item.code}' cannot be deleted` : `Account '${item.code}' is referenced by ${txnsForCode.length} transaction(s) and cannot be deleted`)}
                                disabled={item.is_default || isReferenced}
                                title={item.is_default ? 'Built-in accounts cannot be deleted' : isReferenced ? `Referenced by ${txnsForCode.length} transaction(s)` : 'Delete'}
                                style={{ fontSize: 11, padding: '3px 8px', background: item.is_default || isReferenced ? '#f9fafb' : '#fef2f2', color: item.is_default || isReferenced ? '#9ca3af' : '#b91c1c', border: `1px solid ${item.is_default || isReferenced ? '#e5e7eb' : '#fecaca'}`, borderRadius: 6, cursor: item.is_default || isReferenced ? 'not-allowed' : 'pointer' }}
                              >
                                Delete
                              </button>
                            </td>
                          </>
                        )}
                      </tr>
                      {/* Transaction side-panel for this code */}
                      {coaTxnPanel === item.code && (
                        <tr key={`${item.code}-panel`} style={{ background: '#f8fafc' }}>
                          <td colSpan={8} style={{ padding: '8px 20px' }}>
                            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: '#2563eb' }}>
                              Related transactions for {item.code} ({txnsForCode.length})
                            </div>
                            {txnsForCode.length === 0 ? (
                              <div style={{ fontSize: 11, color: '#888' }}>No transactions reference this account</div>
                            ) : (
                              <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
                                <thead>
                                  <tr style={{ color: '#6b7280', borderBottom: '1px solid #e5e7eb' }}>
                                    <th style={{ padding: '3px 8px', textAlign: 'left' }}>ID</th>
                                    <th style={{ padding: '3px 8px', textAlign: 'left' }}>Date</th>
                                    <th style={{ padding: '3px 8px', textAlign: 'right' }}>Amount</th>
                                    <th style={{ padding: '3px 8px', textAlign: 'left' }}>Type</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {txnsForCode.map((t, idx) => (
                                    <tr key={idx} style={{ borderBottom: '1px solid #f0f0f0' }}>
                                      <td style={{ padding: '3px 8px', fontFamily: 'monospace' }}>{t.id_number ?? '—'}</td>
                                      <td style={{ padding: '3px 8px' }}>{t.date ?? '—'}</td>
                                      <td style={{ padding: '3px 8px', textAlign: 'right' }}>{t.amount != null ? Number(t.amount).toLocaleString() : '—'}</td>
                                      <td style={{ padding: '3px 8px' }}>{t.transaction_type ?? '—'}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}

                {/* Add new row */}
                {coaAddingNew && (
                  <tr style={{ background: '#f0f9ff', borderBottom: '1px solid #e5e7eb' }}>
                    <td style={{ padding: '4px 6px' }}>
                      <input className="settings-input" placeholder="e.g. 6010" value={coaNewDraft.code} onChange={e => setCoaNewDraft(d => ({ ...d, code: e.target.value }))} style={{ width: 70, fontFamily: 'monospace' }} />
                    </td>
                    <td style={{ padding: '4px 6px' }}>
                      <input className="settings-input" placeholder="English name" value={coaNewDraft.name_en} onChange={e => setCoaNewDraft(d => ({ ...d, name_en: e.target.value }))} style={{ width: '100%' }} />
                    </td>
                    <td style={{ padding: '4px 6px' }}>
                      <input className="settings-input" placeholder="Chinese name (optional)" value={coaNewDraft.name_zh} onChange={e => setCoaNewDraft(d => ({ ...d, name_zh: e.target.value }))} style={{ width: '100%' }} />
                    </td>
                    <td style={{ padding: '4px 6px' }}>
                      <select className="settings-input" value={coaNewDraft.category_type} onChange={e => setCoaNewDraft(d => ({ ...d, category_type: e.target.value }))} style={{ width: '100%' }}>
                        {['revenue', 'expense', 'asset', 'liability', 'equity', 'other_income', 'bank_fee', 'interest_paid', 'cogs'].map(t => (
                          <option key={t} value={t}>{t}</option>
                        ))}
                      </select>
                    </td>
                    <td style={{ padding: '4px 6px' }}>
                      {/* Mode is locked to the active tab when adding; edit afterwards for shared accounts */}
                      <span style={{ fontSize: 11, background: coaActiveMode === 'AR' ? '#e8f0fe' : coaActiveMode === 'AP' ? '#fce8e6' : '#e6f4ea', color: '#333', padding: '2px 8px', borderRadius: 10, fontWeight: 600 }}>
                        {coaActiveMode}
                      </span>
                    </td>
                    {/* 期初餘額 B/F — shown only when code prefix is 1/2/3 */}
                    <td style={{ padding: '4px 6px', textAlign: 'right' }}>
                      {/^[123]/.test(coaNewDraft.code.trim()) ? (
                        <div style={{ display: 'flex', gap: 4, alignItems: 'center', justifyContent: 'flex-end' }}>
                          <input
                            className="settings-input"
                            type="number"
                            step="0.01"
                            placeholder="0.00"
                            value={coaNewDraft.opening_balance}
                            onChange={e => setCoaNewDraft(d => ({ ...d, opening_balance: e.target.value }))}
                            style={{ width: 90, textAlign: 'right' }}
                          />
                          <select
                            className="settings-input"
                            value={coaNewDraft.opening_balance_dr_cr}
                            onChange={e => setCoaNewDraft(d => ({ ...d, opening_balance_dr_cr: e.target.value }))}
                            style={{ width: 52 }}
                          >
                            <option value="Dr">Dr</option>
                            <option value="Cr">Cr</option>
                          </select>
                        </div>
                      ) : (
                        <span style={{ fontSize: 11, color: '#bbb' }}>—</span>
                      )}
                    </td>
                    {/* 累計金額 — always — for a new row */}
                    <td style={{ padding: '4px 6px', textAlign: 'right', color: '#bbb', fontSize: 11 }}>—</td>
                    <td style={{ padding: '4px 6px', textAlign: 'center', whiteSpace: 'nowrap' }}>
                      <button type="button" onClick={handleCoaAddNew} style={{ fontSize: 11, padding: '3px 8px', marginRight: 4, background: '#111827', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer' }}>Add</button>
                      <button type="button" onClick={() => { setCoaAddingNew(false); setCoaError('') }} style={{ fontSize: 11, padding: '3px 8px', background: '#f3f4f6', border: '1px solid #e5e7eb', borderRadius: 6, cursor: 'pointer' }}>Cancel</button>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>

            {!coaAddingNew && (
              <button
                type="button"
                onClick={() => { setCoaAddingNew(true); setCoaNewDraft({ code: '', name_en: '', name_zh: '', category_type: coaActiveMode === 'AR' ? 'revenue' : 'expense', allowed_modes: [coaActiveMode], opening_balance: '', opening_balance_dr_cr: 'Dr' }); setCoaError('') }}
                style={{ marginTop: 10, padding: '5px 14px', background: '#111827', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 12 }}
              >
                + Add account
              </button>
            )}
          </div>

  )
}
