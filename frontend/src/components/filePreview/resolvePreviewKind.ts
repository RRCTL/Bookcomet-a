export type PreviewKind = 'image' | 'pdf' | 'unsupported'

function extOf(filename: string): string {
  const base = filename.trim().toLowerCase()
  const dot = base.lastIndexOf('.')
  if (dot < 0) return ''
  return base.slice(dot + 1)
}

export function resolvePreviewKind(mimeType: string | null | undefined, filename: string): PreviewKind {
  const mime = (mimeType ?? '').trim().toLowerCase()
  if (mime.startsWith('image/')) return 'image'
  if (mime === 'application/pdf') return 'pdf'

  const ext = extOf(filename)
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) return 'image'
  if (ext === 'pdf') return 'pdf'
  return 'unsupported'
}

export function guessMimeFromFilename(filename: string): string {
  const ext = extOf(filename)
  switch (ext) {
    case 'pdf':
      return 'application/pdf'
    case 'png':
      return 'image/png'
    case 'jpg':
    case 'jpeg':
      return 'image/jpeg'
    case 'gif':
      return 'image/gif'
    case 'webp':
      return 'image/webp'
    default:
      return 'application/octet-stream'
  }
}
