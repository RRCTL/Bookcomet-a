import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

type ConsoleLine = { ts?: string; level: string; message: string }

type Props = {
  consoleLines: ConsoleLine[]
  reviewPanel?: ReactNode
  reviewOpen: boolean
}

export function BottomDock({ consoleLines, reviewPanel, reviewOpen }: Props) {
  const [consoleHeight, setConsoleHeight] = useState(140)
  const dragging = useRef(false)

  const onMouseMove = useCallback((e: MouseEvent) => {
    if (!dragging.current) return
    const h = window.innerHeight - e.clientY - (reviewOpen ? 220 : 0)
    setConsoleHeight(Math.min(400, Math.max(80, h)))
  }, [reviewOpen])

  const onMouseUp = useCallback(() => {
    dragging.current = false
  }, [])

  useEffect(() => {
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    return () => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
  }, [onMouseMove, onMouseUp])

  return (
    <div className={`bottom-dock${reviewOpen ? ' bottom-dock--with-review' : ''}`}>
      {reviewOpen && reviewPanel ? <div className="bottom-dock__review">{reviewPanel}</div> : null}
      <div
        className="bottom-dock__resize"
        role="separator"
        onMouseDown={() => {
          dragging.current = true
        }}
      />
      <div className="bottom-dock__console" style={{ height: consoleHeight }}>
        <div className="bottom-dock__console-title">Console</div>
        <div className="bottom-dock__console-body">
          {consoleLines.length === 0 ? (
            <div className="bottom-dock__line bottom-dock__line--muted">No log output yet</div>
          ) : (
            consoleLines.map((line, i) => (
              <div key={i} className={`bottom-dock__line bottom-dock__line--${line.level}`}>
                [{line.level}] {line.message}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
