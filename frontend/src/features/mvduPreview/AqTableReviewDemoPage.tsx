import { ARAPReview, type ARAPTransaction } from '../../components/ARAPReview'

/** Synthetic rows with real AQ provenance shape — fictional merchants only, no receipt photos. */
const DEMO_ROWS: ARAPTransaction[] = [
  {
    id_number: 'AQ-DEMO-001',
    date: '2026-08-12',
    transaction_type: 'AP',
    payee: 'North Desk Supplies',
    amount: 328.5,
    debit: 328.5,
    credit: null,
    dr_cr: 'Dr',
    currency: 'HKD',
    source_file: 'synthetic_page.png P1',
    confidence: '0.92',
    extraction_provenance: {
      source_pdf_page: 1,
      receipt_region_norm: { x: 0.06, y: 0.08, w: 0.4, h: 0.38 },
      image_quality: {
        enabled: true,
        selection: 'original_selected',
        ui_label: 'Original · clear',
        status: 'clear',
        ui_state: 'original_clear',
        reason: 'Crop passes quality gates; prefer original.',
        issues: [],
        score_before: 0.668,
        score_after: null,
        recipe: [],
        quality_before: {
          blur_variance: 2648.08,
          local_contrast: 0.2109,
          glare_hotspot_fraction: 0,
          glare_fraction: 0,
          ink_fraction: 1,
          edge_density: 0.0405,
        },
      },
    },
  },
  {
    id_number: 'AQ-DEMO-002',
    date: '2026-08-13',
    transaction_type: 'AP',
    payee: 'Harbor Cafe',
    amount: 86,
    debit: 86,
    credit: null,
    dr_cr: 'Dr',
    currency: 'HKD',
    source_file: 'synthetic_page.png P1',
    confidence: '0.71',
    extraction_provenance: {
      source_pdf_page: 1,
      receipt_region_norm: { x: 0.52, y: 0.1, w: 0.4, h: 0.34 },
      image_quality: {
        enabled: true,
        selection: 'enhanced_selected',
        ui_label: 'Auto-enhanced · view original',
        status: 'recoverable',
        ui_state: 'faded_receipt',
        reason: 'Recoverable quality issues detected; try minimal enhancement recipe.',
        issues: ['blur', 'low_contrast'],
        score_before: 0.184,
        score_after: 0.207,
        recipe: [{ op: 'deskew' }, { op: 'lab_clahe' }, { op: 'unsharp_mild' }],
        quality_before: {
          blur_variance: 25.39,
          local_contrast: 0.0215,
          glare_hotspot_fraction: 0,
          glare_fraction: 0,
          ink_fraction: 1,
          edge_density: 0,
        },
        quality_after: {
          blur_variance: 51.58,
          local_contrast: 0.025,
          glare_hotspot_fraction: 0,
          glare_fraction: 0,
          ink_fraction: 1,
          edge_density: 0,
        },
      },
    },
  },
  {
    id_number: 'AQ-DEMO-003',
    date: '',
    transaction_type: 'AP',
    payee: '',
    amount: null,
    debit: null,
    credit: null,
    dr_cr: 'Dr',
    currency: 'HKD',
    source_file: 'synthetic_page.png P1',
    needs_review: true,
    validation_flags: ['image_quality_unrecoverable'],
    extraction_provenance: {
      source_pdf_page: 1,
      receipt_region_norm: { x: 0.08, y: 0.52, w: 0.84, h: 0.4 },
      image_quality: {
        enabled: true,
        selection: 'recapture_requested',
        ui_label: 'Image cannot be verified',
        status: 'unrecoverable',
        ui_state: 'glare_cannot_verify',
        reason: 'Glare / overexposure covers too much of the crop; retake with indirect light.',
        issues: ['glare'],
        score_before: 0.588,
        score_after: null,
        recipe: [],
        quality_before: {
          blur_variance: 800,
          local_contrast: 0.13,
          glare_hotspot_fraction: 0.14,
          glare_fraction: 0.21,
          ink_fraction: 0.79,
          edge_density: 0.028,
        },
      },
    },
  },
]

export default function AqTableReviewDemoPage() {
  return (
    <div className="min-h-screen bg-slate-100 p-4">
      <div className="mx-auto mb-3 max-w-[1400px]">
        <p className="m-0 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          M-VDU · real Table Review AQ UI · synthetic rows · no PR
        </p>
        <h1 className="m-0 text-lg font-semibold text-slate-900">Live output · Image quality</h1>
        <p className="m-0 mt-1 text-sm text-slate-600">
          Same <code>ARAPReview</code> panel used after VLM. Rows carry fictional AQ provenance only — no
          real receipts or company data.
        </p>
      </div>
      <div className="mx-auto max-w-[1400px]">
        <ARAPReview transactions={DEMO_ROWS} filename="synthetic_demo.pdf" useApTableSchema />
      </div>
    </div>
  )
}
