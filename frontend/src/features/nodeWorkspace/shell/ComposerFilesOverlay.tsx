type Props = {
  visible: boolean
  onDropFiles: (files: FileList) => void
}

export function ComposerFilesOverlay({ visible, onDropFiles }: Props) {
  if (!visible) return null

  return (
    <div
      className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-3xl border-2 border-dashed border-gray-300 bg-white/90 dark:border-gray-600 dark:bg-gray-900/90"
      aria-hidden
    >
      <p className="text-sm font-medium text-gray-600 dark:text-gray-300">Drop files to attach</p>
    </div>
  )
}

export function handleComposerDrop(
  e: React.DragEvent,
  onDropFiles: (files: FileList) => void,
): boolean {
  e.preventDefault()
  e.stopPropagation()
  if (e.dataTransfer.files?.length) {
    onDropFiles(e.dataTransfer.files)
    return true
  }
  return false
}

export function handleComposerDragOver(e: React.DragEvent): void {
  e.preventDefault()
  e.stopPropagation()
}
