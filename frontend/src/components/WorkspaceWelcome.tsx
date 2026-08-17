import { DROPDOWN_MODES, MODE_META, type ProcessingMode } from './ModeSelector'
import './WorkspaceWelcome.css'

const OCR_MODES = DROPDOWN_MODES

export interface WorkspaceWelcomeProps {
  onChooseDocumentMode: (mode: ProcessingMode) => void
}

export function WorkspaceWelcome({
  onChooseDocumentMode,
}: WorkspaceWelcomeProps) {
  return (
    <div className="workspace-welcome">
      <div className="workspace-welcome-inner">
        <p className="workspace-welcome-kicker">Bookcomet</p>
        <h2 className="workspace-welcome-title">Start with document capture</h2>
        <p className="workspace-welcome-lead">
          Upload invoices, receipts, or bank statements for OCR. Draft double-entry journals can be edited per
          transaction and synced to the server from the spreadsheet workflow.
        </p>

        <section className="workspace-welcome-section" aria-labelledby="welcome-doc-heading">
          <h3 id="welcome-doc-heading" className="workspace-welcome-section-title">
            Choose document type
          </h3>
          <p className="workspace-welcome-section-hint">
            Starts a new chat task in that mode. Upload images or PDFs from the header or sidebar when ready (AP, AR, and Bank also support CSV without VLM).
          </p>
          <ul className="workspace-welcome-next-stack workspace-welcome-step1-list">
            {OCR_MODES.map((mode) => {
              const meta = MODE_META[mode]
              return (
                <li key={mode} className="workspace-welcome-next-card">
                  <div className="workspace-welcome-card-head">
                    <span className="workspace-welcome-card-badge">{meta.shortLabel}</span>
                    <span className="workspace-welcome-card-label">{meta.label}</span>
                  </div>
                  <p className="workspace-welcome-next-desc">{meta.description}</p>
                  <button
                    type="button"
                    className="primary workspace-welcome-next-btn"
                    onClick={() => onChooseDocumentMode(mode)}
                  >
                    Use this mode
                  </button>
                </li>
              )
            })}
          </ul>
        </section>

        <p className="workspace-welcome-foot">
          Already working on something? Open a task from the left list to continue the conversation and editing.
        </p>
      </div>
    </div>
  )
}
