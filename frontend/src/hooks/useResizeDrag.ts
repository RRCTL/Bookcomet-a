import { useEffect, useRef } from 'react'
import type { Dispatch, SetStateAction } from 'react'

/**
 * Document-level drag for panel resizing: adds `resizing` body class while active.
 * `applyClientX` receives pointer X and window inner width (for right-edge handles).
 */
export function useResizeDrag(
  dragging: boolean,
  setDragging: Dispatch<SetStateAction<boolean>>,
  applyClientX: (clientX: number, innerWidth: number) => void,
) {
  const applyRef = useRef(applyClientX)
  applyRef.current = applyClientX

  useEffect(() => {
    if (!dragging) {
      document.body.classList.remove('resizing')
      return
    }
    document.body.classList.add('resizing')
    const onMove = (e: MouseEvent) => {
      if (e.buttons !== 1) {
        setDragging(false)
        return
      }
      applyRef.current(e.clientX, window.innerWidth)
    }
    const onUp = () => setDragging(false)
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.classList.remove('resizing')
    }
  }, [dragging, setDragging])
}
