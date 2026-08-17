import { useEffect } from 'react'
import { resolvePreviewKind } from './resolvePreviewKind'
import './FilePreviewModal.css'

export type FilePreviewModalFile = {
  id: string
  name: string
}

type Props = {
  open: boolean
  onClose: () => void
  filename: string
  mimeType?: string | null
  previewUrl: string | null
  loading?: boolean
  error?: string | null
  files?: FilePreviewModalFile[]
  activeFileId?: string | null
  onSelectFile?: (id: string) => void
  onRetry?: () => void
  onDownload?: () => void
}

export function FilePreviewModal({
  open,
  onClose,
  filename,
  mimeType,
  previewUrl,
  loading = false,
  error = null,
  files = [],
  activeFileId,
  onSelectFile,
  onRetry,
  onDownload,
}: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const kind = previewUrl ? resolvePreviewKind(mimeType, filename) : 'unsupported'
  const showSwitcher = files.length > 1 && onSelectFile

  return (
    <div
      className="file-preview-overlay"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="ow-card file-preview-card"
        role="dialog"
        aria-modal="true"
        aria-label={`Preview ${filename}`}
        onClick={e => e.stopPropagation()}
      >
        <div className="file-preview-header">
          <button
            type="button"
            className="file-preview-close"
            onClick={onClose}
            aria-label="Close preview"
          >
            ×
          </button>
          <span className="file-preview-title" title={filename}>
            {filename}
          </span>
          {onDownload ? (
            <button type="button" className="btn-secondary file-preview-download" onClick={onDownload}>
              Download
            </button>
          ) : null}
        </div>

        {showSwitcher ? (
          <div className="file-preview-switcher">
            {files.map(f => (
              <button
                key={f.id}
                type="button"
                className={`file-preview-switcher-btn${f.id === activeFileId ? ' active' : ''}`}
                title={f.name}
                onClick={() => onSelectFile(f.id)}
              >
                {f.name}
              </button>
            ))}
          </div>
        ) : null}

        <div className="file-preview-body">
          {loading ? (
            <div className="file-preview-loading">Loading preview…</div>
          ) : error ? (
            <div className="file-preview-error">
              <p>{error}</p>
              {onRetry ? (
                <button type="button" className="btn-secondary file-preview-retry" onClick={onRetry}>
                  Retry
                </button>
              ) : null}
            </div>
          ) : previewUrl && kind === 'image' ? (
            <div className="file-preview-viewer">
              <img src={previewUrl} alt={filename} className="preview-image" />
            </div>
          ) : previewUrl && kind === 'pdf' ? (
            <div className="file-preview-viewer">
              <iframe
                src={`${previewUrl}#view=FitH`}
                title={filename}
                className="preview-pdf"
                allow="fullscreen"
              />
            </div>
          ) : previewUrl ? (
            <div className="file-preview-unsupported">
              <p>Preview is not available for this file type.</p>
              <p className="text-sm">{mimeType || 'Unknown type'}</p>
              {onDownload ? (
                <button type="button" className="btn-primary mt-3" onClick={onDownload}>
                  Download file
                </button>
              ) : null}
            </div>
          ) : (
            <div className="file-preview-unsupported">
              <p>No preview available.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
