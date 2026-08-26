# Private HSBC / BANK regression evidence (Slice 6)

Use `private_evidence_manifest.template.json` offline only.

## Rules

- Never commit real PDFs, CSV exports, account numbers, company names, payees, or raw backend logs with customer text.
- Map each private incident to **synthetic fixture IDs** and contract ids (`A_coverage`, `B_column_band`, `C_section`, `D_provenance`).
- Record `dispatcher_route=hsbc_adapter` and `scenario_d_used=false` in private sign-off notes.
- Repository CI proves contracts via synthetic fixtures only.

## Sign-off checklist (private QA)

1. Upload via BANK UI with Settings → API VLM configured (fail-closed if empty).
2. Confirm logs show `bank_document_dispatcher` / `hsbc_adapter`, not Scenario D.
3. Confirm Contracts A–D pass or rows are `needs_review` (no silent bypass).
4. Confirm Table Review shows source page / section / anchor / column provenance.
5. Confirm CSV export either blocks or requires explicit acknowledge on failed contracts.
6. Update private manifest only; do not paste real paths into git.
