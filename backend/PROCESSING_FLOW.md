# OCR Processing Flow Documentation

## Scope and current architecture

The live workspace pipeline runs through **`ocr_test_core`** in `backend/app/api/ocr.py`, shared by synchronous **`POST /ocr/test`** and asynchronous **`POST /api/jobs/ocr`** (background job → `run_ocr_background_job` in `backend/app/services/job_tasks.py`).

**`processing_mode`** selects behavior (e.g. **`AP`**, **`AR`**, **`BANK`**). This document’s **Overview** diagram is mode-dependent. The historical description below that assumed **PaddleOCR + DeepSeek** for everything is **not accurate for AR/AP today**: those modes use a **VLM** path for document OCR and structured extraction, plus optional **OpenCV/PIL** splitting for multiple receipts on one page.

| Mode | Primary OCR | Structured output |
|------|-------------|-------------------|
| **AP / AR** | VLM (`ocr_service.recognize`, model from env e.g. `AP_VLM_MODEL`) | VLM JSON → `tsv_rows` (and cheque/deposit-advice branches) |
| **BANK** | VLM + bank table prompt | Bank-specific pipeline |
| **Other / legacy** | May use VLM | DeepSeek enhancement when API key set (`ai_post_processor`) |

**Code map:** `ocr_test_core`, `_classify_document_layout`, `_run_ap_multi_receipt_ocr_from_image`, `classify_document` (`document_gate.py`), `_extract_ar_ap_ai_fields_routed`, `_extract_ap_ai_fields_for_page`.

---

## AP mode (Accounts Payable) — detailed pipeline

### Purpose

- **`processing_mode == "AP"`** runs the payables branch: rows carry **`transaction_type: "AP"`** for UI and downstream APIs (e.g. AP chart of accounts).

### Request path

1. **`POST /api/jobs/ocr`** (`backend/app/api/jobs.py`): multipart `file`, `processing_mode=AP`, optional `multi_receipt_confirmed`, `multi_receipt_acknowledged`, `force_process`, `task_id`.
2. Validates file, cost cap, company; stores bytes; queues **`run_ocr_background_job`**.
3. Worker reloads file as **`UploadFile`** and calls **`ocr_test_core`** with the same flags.

**Frontend:** `api.createOcrBackgroundJob` (`frontend/src/services/api.ts`). If the result has **`needs_confirmation`**, the client **retries** with **`multi_receipt_confirmed: true`** to enable forced splitting (`WorkspaceApp.tsx`).

### Preconditions in `ocr_test_core`

- **`check_monthly_cost`** (per company).
- **`company_ocr_concurrency`**: limits parallel OCR per company.

### Models and prompts (AP)

- **Registry provider name** is `OCR_PROVIDER` when set, otherwise Settings `VLM_MODEL`. The **actual model id** sent to the gateway is **`AP_VLM_MODEL`** (env `AP_VLM_MODEL` or legacy `AP_MULTI_RECEIPT_OCR_MODEL`, else Settings `VLM_MODEL`).
- **Stage‑1 (document parsing) prompt:** `AP_MULTI_RECEIPT_DOCUMENT_PARSING_PROMPT` for AR/AP, optionally prefixed with **company profile** and appended with **rule-memory AI hints** (`_load_rule_memory_for_ocr` / `_extract_ai_instructions` for modes in `AR`, `AP`, `BANK`, `OTHER`).
- OCR text passed into prompts is **`sanitise_ocr_text`**’d where applicable.

### PDFs and images

- For **`AP`**, **`AR`**, and **`BANK`**, the pipeline **does not** short-circuit to “PDF text extraction only”; PDFs are **rasterized** so the VLM image path runs.

### Layout routing (invoice vs composite receipts)

**Classifier:** `_classify_document_layout` — VLM (`DOCUMENT_LAYOUT_CLASSIFY_MODEL` if set, else Settings `VLM_MODEL`), normalized to **`invoice`** (single structured document) vs **`receipts`** (composite scan).

**Shortcuts**

- **`multi_receipt_confirmed`**: skip classifier; treat as **`receipts`** so splitting can run without an extra call.
- **Cheque quick-probe** (when enabled): if layout was **`receipts`** but probe matches, treat as **`invoice`** so OpenCV multi-receipt split is skipped.

**Multi-page PDF (AR/AP)**

- Classify first page; if first is **`invoice`** and page count ≥ **`AP_LAYOUT_LAST_PAGE_MIN_PAGES`**, optionally classify **last** page — if last is **`receipts`**, route as receipts for the whole batch.
- **Scenario C:** **`invoice`** and stitched image would not “collapse” on resize (**`AP_STITCH_UPLOAD_MIN_SHORT_EDGE`**): **vertical stitch** all pages → **one** image → one OCR + gate + extraction (OpenCV split **not** run on the stitched image).
- **Scenario D:** otherwise **per-page** processing (queue workers; default up to **`AP_LAYOUT_MAX_PAGE_CONCURRENCY`=3** concurrent pages). Scheduler supports early-stop (`terminated_reason`) when failure/rate-limit thresholds are hit; unscheduled pages are returned as `status: "error"` with `error_code: "NOT_SCHEDULED"`.

**Single page**

- **`invoice` (Scenario A):** no OpenCV multi-receipt path; single OCR + gate + extraction.
- **`receipts` (Scenario B):** **`_run_ap_multi_receipt_ocr_from_image`**. If it returns **`None`** and the user has not confirmed → response **`needs_confirmation`**.

### Multi-receipt on one page: regions and cutting (`_run_ap_multi_receipt_ocr_from_image`)

**1) Build region list**

- If **`processing_mode == "AP"`** and **`AP_VLM_LAYOUT_CROP_ENABLED`**: **`_ap_vlm_layout_try_receipt_regions`**
  - Thumbnail (`AP_VLM_LAYOUT_THUMB_MAX_SIDE`), VLM returns JSON: **`confidence`**, **`count`**, **`receipts: [{x,y,w,h}]`** normalized to **[0,1]** per **`AP_VLM_LAYOUT_DETECTION_PROMPT`**.
  - **`_validate_layout_json`**: min confidence, **≥ 2** boxes, geometry, **`count` == len(receipts)**. Maps to full-resolution pixels with **`AP_VLM_LAYOUT_BOX_PAD_PCT`**.
  - On failure → fallback.
- **Else:** **`_detect_receipt_regions_v2`** (Canny, column/row dominant-gap grid, density filter), with fallbacks to contour-based **`_detect_receipt_regions`** and **`_detect_receipt_regions_pil`**.
- Balanced split-evidence gate applies before multi-crop OCR on AP pages: weakly separated / fragment-heavy candidates are collapsed back to one region.
- If **≤ 1** region: without **`confirmed`** return **`None`**; with user confirm run **`_force_split_receipt_regions`** (multi-gap or bisect long axis).

**2) Cut and process each region**

- **`_crop_receipt_regions`**: PIL crop → temp PNG per box.
- **Per crop:** VLM pass with **`AP_MULTI_RECEIPT_DOCUMENT_PARSING_PROMPT`**, **`_filtering_pipeline.filter_and_extract`**, then **`_extract_ar_ap_ai_fields_routed`**; **`attach_receipt_region_provenance`** on rows; **`apply_batch_duplicate_flags_ar_ap`** across crops.
- Concurrency: **`AP_CROP_OCR_CONCURRENCY`**; optional **`AP_CROP_OCR_IMAGE_MAX_SIDE`** / JPEG quality for crop uploads.
- Crop preflight guard: tiny/degenerate crops are skipped with structured error slots (`CROP_TOO_SMALL` / `CROP_BAD_ASPECT`) instead of making low-value OCR calls.

### Document gate (AR/AP, unless `force_process`)

After first full OCR text + filtering, **`classify_document`** (`backend/app/services/document_gate.py`): company **`document_gate`** rules → keyword heuristics → LLM. Non-**`TRANSACTIONAL`** results return early with **`gate_result`** / **`gate_message`** (and optional subtype for reference-financial). UI shows a gate card for user choice.

### Structured extraction (AP does not use DeepSeek “enhance” path)

For **`processing_mode in ("AR", "AP")`**, Step 4 uses **`_extract_ar_ap_ai_fields_routed`**:

1. **`_is_cheque_deposit_advice`** → **`_extract_cheque_deposit_advice_fields_for_page`**
2. Cheque probe matched or **`_is_cheque_document`** → **`_extract_cheque_fields_for_page`**
3. Else **`_extract_ap_ai_fields_for_page`**: second VLM call with structured JSON prompt + layout hint from **`ocr_lines`**; parse JSON → **`tsv_rows`**; fallback to TSV parse, regex on OCR text, or bare low-confidence row. **`validate_ar_ap_receipt`** / **`merge_validation_into_row`** per row.

**Optional independent OCR cross-check (flag-only):** when **`AP_OCR_CROSS_CHECK_PROVIDER`** is set, `_extract_ap_ai_fields_for_page` re-reads the same crop/page image with a local OCR engine (PaddleOCR today; see `app/ocr/cross_check.py`, which is independent of `OcrService`) and flags rows whose VLM **amount / currency / date / merchant** disagree with that reading — **`ocr_xcheck_amount_mismatch`**, **`ocr_xcheck_currency_mismatch`**, **`ocr_xcheck_date_mismatch`**, **`ocr_xcheck_merchant_mismatch`** (sets `needs_review`). It never overwrites values. Disabled by default; covers both the single-page and multi-receipt crop paths via this shared function.

### Observability

- **`_record_processing_event`** writes **`OcrCompletionEvent`** with **`build_decision_evidence`** in metadata for traceability.

### AP-related environment variables

| Variable | Role |
|----------|------|
| `AP_VLM_MODEL` / `AP_MULTI_RECEIPT_OCR_MODEL` | Main VLM id for AP |
| `AP_VLM_LAYOUT_CROP_ENABLED` | Enable VLM bounding-box multi-receipt detection |
| `AP_VLM_LAYOUT_CONFIDENCE_MIN`, `AP_VLM_LAYOUT_THUMB_MAX_SIDE`, `AP_VLM_LAYOUT_BOX_PAD_PCT`, `AP_VLM_LAYOUT_MAX_RETRIES` | Layout JSON validation / retries |
| `AP_CROP_OCR_CONCURRENCY`, `AP_CROP_OCR_IMAGE_MAX_SIDE`, `AP_CROP_OCR_JPEG_QUALITY` | Multi-crop OCR |
| `AP_CROP_OCR_TIMEOUT_S` | Per-crop OCR deadline (seconds). Empty uses `VLM_READ_TIMEOUT` → `VLM_TIMEOUT` → `120` |
| `VLM_HTTP_MAX_RETRIES` | HTTP attempts per upload profile on Timeout/ConnectionError. Empty = 3. Crop/receipt OCR passes 1 via `ocr_options` |
| `AP_CROP_MIN_WIDTH_PX`, `AP_CROP_MIN_HEIGHT_PX`, `AP_CROP_MIN_AREA_PX`, `AP_CROP_MIN_ASPECT_RATIO`, `AP_CROP_MAX_ASPECT_RATIO` | Crop preflight guards before OCR |
| `AP_SEG_MULTI_MIN_REGION_AREA_FRAC`, `AP_SEG_MULTI_MAX_DOMINANCE`, `AP_SEG_MIN_GAP_FRAC`, `AP_SEG_FRAGMENT_REL_AREA_MAX`, `AP_SEG_SINGLE_MERGE_PAD_FRAC` | AP anti-over-split evidence gate / single-collapse behavior |
| `AP_LAYOUT_MAX_PAGE_CONCURRENCY`, `OCR_SCENARIO_D_MAX_CONSECUTIVE_FAILURES`, `OCR_SCENARIO_D_MAX_FAILURE_RATIO`, `OCR_SCENARIO_D_FAILURE_RATIO_MIN_SAMPLES` | Scenario D queue scheduling / early termination |
| `AP_STITCH_UPLOAD_MIN_SHORT_EDGE`, `AP_LAYOUT_LAST_PAGE_MIN_PAGES` | Multi-page stitch vs per-page |
| `AP_CROSS_VLM_MODEL` | Second VLM for structured re-extraction; merged **in-place** into primary `ai_enhanced` / `tsv_rows` |
| `AP_AUTO_CROSS_VERIFY_ENABLED` | When true (default) and `AP_CROSS_VLM_MODEL` is set, run second pass + merge on AP uploads |
| `AP_AUTO_CROSS_VERIFY_POLICY` | Merge policy (default `aggressive_overwrite`: non-empty cross fields overwrite primary) |
| `AP_AUTO_CROSS_VERIFY_CONFIDENCE_THRESHOLD` | Min parsed row confidence on cross result to apply merge; `0` disables this gate |
| `AP_AUTO_CROSS_VERIFY_TIMEOUT_MS` | Per-pass timeout for the cross structured call |
| `AP_AUTO_CROSS_VERIFY_SKIP_PRIMARY_CONFIDENCE` | Skip the cross pass when primary min row confidence ≥ this value (0–1); `0` (default) never skips; manual Double check always runs |
| `AP_OCR_CROSS_CHECK_PROVIDER` | Independent local-OCR cross-check (flag-only) for AP rows; empty/off disables (default), `paddle` uses local PaddleOCR (Windows-risky, needs `requirements-ocr.txt`); room for `cloud` later |
| `AP_OCR_CROSS_CHECK_MERCHANT_MIN_OVERLAP` | Min fraction of merchant-name units that must appear in the independent OCR text to count as a match (0–1, default `0.5`) |
| `GATE_MODEL` / deploy URL+key | Document gate LLM |

After primary structured extraction for **AP**, the pipeline may call **`_ap_apply_cross_vlm_merge_if_configured`**: same image and OCR text, second **`_extract_ar_ap_ai_fields_routed`** with **`AP_CROSS_VLM_MODEL`**, then **`merge_ap_ai_enhanced_primary_with_cross`** (`app/services/ap_vlm_cross_merge.py`). Manual **Double check** uses **`ap_force_cross_verify`** so merge runs even when **`AP_AUTO_CROSS_VERIFY_ENABLED`** is off. **`GET /health`** exposes **`ap_auto_cross_verify_enabled`** and **`ap_cross_verify_pipeline_active`**.

- The **second pass** adds an extra structured-prompt block (**`AP_STRUCTURED_CROSS_VERIFY_SUPPLEMENT`** in `app/api/ocr.py`) so the cross model infers **ISO 4217** currency from multilingual text, symbols, and tax wording instead of assuming Hong Kong only; primary pass wording is unchanged.

---

## Processing flow (high-level)

Mode-dependent. **AP/AR simplified:**

```
File upload (image or PDF)
    -> Validation, temp save, cost/concurrency checks
    -> [PDF] Rasterize pages (PyMuPDF) — AP/AR always use image path
    -> Layout classify: invoice vs receipts (AR/AP); multi-page stitch vs per-page
    -> [If receipts / multi-region] Segment -> crop -> per-crop VLM OCR + extraction
    -> [Else] Single-image VLM OCR
    -> Rule-based field filtering (_filtering_pipeline)
    -> Document gate (AR/AP, unless force_process)
    -> Structured extraction (VLM JSON / cheque branches for AR/AP)
    -> [AP + cross model] Second structured pass + in-place merge into same `tsv_rows`
    -> JSON response + temp file cleanup
```

**BANK / other modes** may differ (bank table prompt; DeepSeek enhancement when applicable).

---

## Detailed stage breakdown

### Stage 1: File upload and validation

**Locations:** `create_ocr_job` in `backend/app/api/jobs.py`; `ocr_test_core` in `backend/app/api/ocr.py`.

**Actions:**

- Validate filename and extension.
- Supported: **Images** `.jpg`, `.jpeg`, `.png`, `.bmp`, `.tiff`, `.webp`; **PDF** `.pdf`.
- Non-empty body; PDF support (PyMuPDF) if PDF.

---

### Stage 2: PDF conversion (if applicable)

**Locations:** `ocr_test_core`; `_convert_pdf_to_images`; `backend/app/utils/file_converter.py`.

**Technology:** PyMuPDF (fitz).

**Actions:**

1. Render pages at high resolution.
2. Export per-page images (e.g. PNG) to temp paths.

**AR/AP:** Rasterization is always used (no “text-only PDF” fast path for these modes).

---

### Stage 3: OCR processing

**Location:** `backend/app/services/ocr_service.py` (invoked from `ocr_test_core`).

**AR/AP (including AP):** **VLM** recognition with mode-specific **`model`** and **`prompt_override`** (document parsing or bank prompt for BANK). Output includes **`text`**, **`lines`** (with bounding boxes where the provider supplies them), and **`metadata`** (provider-dependent; not necessarily `paddle`).

**Note:** Some endpoints or code paths may still use other backends; the **workspace AP/AR path is VLM-first**.

---

### Stage 4: Field extraction

**Location:** `backend/app/api/ocr.py` + **`_filtering_pipeline`** (`field_filtering` / related services).

**Technology:** Rule-based extraction on OCR result.

Produces **`extracted_fields`**, **`overall_confidence`**, **`missing_fields`**, **`status`**.

---

### Stage 5: AI enhancement (mode-dependent)

**Location:** `ocr_test_core` Step 4.

- **AR/AP:** Structured extraction via **`_extract_ar_ap_ai_fields_routed`** (VLM JSON / cheque / deposit advice), **not** the generic DeepSeek **`_ai_processor.enhance_ocr_result`** block.
- **Other modes:** When **`_ai_processor.api_key`** is set, DeepSeek-style enhancement may run; failures are often non-fatal.

---

### Stage 6: Result assembly

**Location:** `ocr_test_core` return paths (single-page, multi-page, multi-crop, gate early exit, `needs_confirmation`).

**Typical fields:** `trace_id`, `filename`, `document_type`, `text`, `lines`, `extracted_fields`, **`ai_enhanced`** (for AR/AP this is the structured **`tsv_rows`** payload), `processing_mode`, `processing_steps`, and for multi-page **`pages`**, **`total_pages`**.

#### `total_pages` vs `len(pages)` (contract)

- **`total_pages`** is the **PDF page count** (1-based count of rasterized PDF pages) when the source was a PDF. For a single uploaded **image**, treat **`total_pages` as 1** (or omit if the response is single-page shaped—clients should not assume `pages` exists for every path).
- **`pages`** is a **flat list of atomic OCR outcomes**: one entry per **scheduled page task** in Scenario D (including **`status: "error"`** slots so there is no silent missing page).
- **`len(pages)` may be greater than `total_pages`** when **one PDF page** is expanded into **multiple receipt crops** (multi-receipt on a single page). Each crop can appear as its own `pages[]` element (with a shared PDF `page` index and crop metadata as emitted by the pipeline).
- **`len(pages)` equals `total_pages`** in the common case of **one outcome per PDF page** (no multi-crop expansion).
- **Multi-receipt children:** when a page runs **`_run_ap_multi_receipt_ocr_from_image`**, individual crops carry **`status`** (`success` \| `error`) consistent with flattening into `pages[]` or nested structures the API returns; consumers should read **`status`** and `ocr_job_outcome` for partial success.

---

### Stage 7: Cleanup

**Location:** `finally` block in `ocr_test_core`.

Removes temp upload and converted page images; releases concurrency slot.

---

## Technology stack summary (updated)

| Stage | Technology | Notes |
|-------|------------|-------|
| PDF rasterization | PyMuPDF | Workspace AP/AR always image path |
| OCR (AP/AR) | VLM via `ocr_service` | Model from env (e.g. `AP_VLM_MODEL`) |
| Receipt splitting | OpenCV / PIL | `_detect_receipt_regions_v2`, optional VLM boxes (AP) |
| Layout classify | VLM (Settings `VLM_MODEL`, or `DOCUMENT_LAYOUT_CLASSIFY_MODEL`) | Invoice vs composite receipts |
| Document gate | Rules + keywords + LLM | `document_gate.py` |
| Structured rows (AP/AR) | VLM JSON + validation | `_extract_ap_ai_fields_for_page`, cheque branches |
| Optional enhance (non-AR/AP path) | DeepSeek-compatible API | When key configured |

---

## API endpoints

| Endpoint | Purpose |
|----------|---------|
| **`POST /api/jobs/ocr`** | Workspace upload: returns `job_id`; worker runs `ocr_test_core` |
| **`POST /ocr/test`** | Synchronous full pipeline (same core) |
| **`POST /ocr/ai-enhanced`** | Image-focused test path (see router in `ocr.py`) |
| **`POST /ocr/debug`** | Upload + OpenCV read smoke test |

---

## Frontend integration

1. User uploads files in workspace; **`createOcrBackgroundJob`** posts to **`/api/jobs/ocr`** with `processing_mode` (e.g. **AP**).
2. Client polls background job until complete.
3. If **`needs_confirmation`**, client retries with **`multi_receipt_confirmed: true`**.
4. If **`gate_result`** is non-transactional, UI shows gate card; user may re-run with **`force_process`** or route elsewhere (see UI).
5. **`ai_enhanced.tsv_rows`** (AR/AP) populate the editable spreadsheet.

---

## Error handling

- Missing PyMuPDF / PDF support: **500** with install hint.
- Empty file / bad extension: **400**.
- Monthly cost cap: **429** on job creation.
- OCR failures: **500** with detail from `ocr_test_core`.
- DeepSeek / gate LLM failures: gate may default or log; AR/AP extraction has regex fallbacks inside `_extract_ap_ai_fields_for_page`.

**Logging (examples):** `[NEW REQUEST]`, `[STEP 1]`…`[STEP 4]`, `[AP layout]`, `[Classifier]`, `[Gate]`, `[SUCCESS]`, `[WARN]`.

---

## No old PDF stack issues (historical checklist)

- No `pdf2image` / Poppler requirement for core conversion; PyMuPDF used for rasterization.
- Unicode in logs/messages has been kept Windows-safe where noted in project history.

---

## Testing

```bash
curl -X POST http://localhost:8000/ocr/test \
  -F "file=@sample.pdf" \
  -F "processing_mode=AP"
```

Expect logs referencing **VLM** / **AP** stages rather than PaddleOCR for AP mode.

**Multi-page Scenario D:** Queue-driven per-page processing merges successes and failures into `pages[]`. Failed PDF pages or failed multi-receipt crops appear as objects with `status: "error"` and `error_detail`; the response includes `ocr_job_outcome`: `ok` \| `partial` \| `failed`, and may include `terminated_reason` (`rate_limited`, `too_many_page_failures`, `cancelled`).

---

## Related documentation

- **Implementation checklist (partial success, streaming, resilience):** [`docs/OCR_VLM_IMPLEMENTATION_CHECKLIST.zh.md`](docs/OCR_VLM_IMPLEMENTATION_CHECKLIST.zh.md)
- **Background OCR uploads:** When `OCR_JOB_UPLOAD_RETENTION_HOURS` > 0 (default 24), the file at `BackgroundJob.request_json.storage_path` is kept until `storage_retained_until` for `POST /api/jobs/{id}/ocr-retry-page`. Set to `0` to delete the upload when the job finishes (legacy behavior).

---

**Last updated:** 2026-05-07  
**Status:** Reflects AP/AR VLM pipeline in `ocr_test_core`; older PaddleOCR-only narrative removed for those modes.
