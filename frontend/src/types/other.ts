export type OtherRow = {
  id: string
  record_type: 'loan' | 'fixed_asset'
  /** Resolved upload label for display, e.g. contract.pdf P2 */
  source_file_label?: string
  source_file_id?: string
  lender_name?: string
  loan_reference?: string
  principal_amount?: string
  currency?: string
  interest_rate_pct?: string
  tenor_months?: string
  monthly_installment?: string
  start_date?: string
  maturity_date?: string
  outstanding_principal?: string
  status?: string
  asset_name?: string
  asset_type?: string
  purchase_amount?: string
  acquisition_date?: string
  useful_life_months?: string
  residual_value?: string
  depreciation_method?: string
  accumulated_depreciation?: string
  net_book_value?: string
  vendor?: string
  memo?: string
  [key: string]: unknown
}
