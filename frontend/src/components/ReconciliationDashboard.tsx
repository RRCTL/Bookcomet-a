/**
 * Legacy full-reconciliation dashboard — not used in OCR-first builds.
 * Kept as a stub so imports do not break; CoA / deploy / OCR journals live elsewhere.
 */
import './ReconciliationDashboard.css'

export function ReconciliationDashboard() {
  return (
    <div className="reconciliation-dashboard recon-dashboard-stub" style={{ padding: 24 }}>
      <p style={{ margin: 0, color: '#64748b' }}>
        Reconciliation dashboard is not available in this product mode. Use AR / AP / BANK capture and{' '}
        <code>/ocr-journals</code> for draft journals.
      </p>
    </div>
  )
}
