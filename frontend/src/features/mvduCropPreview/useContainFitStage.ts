import { useCallback, useLayoutEffect, useState, type RefObject } from 'react'
import { containFitRect, type FitRect, type Size } from './overlayCoords'

type Opts = {
  stageRef: RefObject<HTMLElement | null>
  /** Fallback when the <img> has not loaded natural size yet. */
  fallbackImage?: Size | null
  /** Re-measure / rebind ResizeObserver when this changes (URL, tab mode, …). */
  layoutKey?: string | number | boolean | null
  /** Reset natural size when the page image source changes. */
  imageKey?: string | null
}

/**
 * Track stage element size + contained image natural size so overlays can
 * follow object-fit:contain letterboxing.
 */
export function useContainFitStage({
  stageRef,
  fallbackImage = null,
  layoutKey = null,
  imageKey = null,
}: Opts): {
  stageSize: Size
  imageSize: Size
  fit: FitRect
  onImageLoad: (img: HTMLImageElement | null) => void
} {
  const [stageSize, setStageSize] = useState<Size>({ width: 0, height: 0 })
  const [naturalSize, setNaturalSize] = useState<Size>({ width: 0, height: 0 })

  useLayoutEffect(() => {
    const el = stageRef.current
    if (!el) return
    const measure = () => {
      setStageSize({ width: el.clientWidth, height: el.clientHeight })
    }
    measure()
    if (typeof ResizeObserver === 'undefined') {
      return
    }
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [stageRef, layoutKey, imageKey])

  useLayoutEffect(() => {
    // Reset natural size when the page image source changes.
    setNaturalSize({ width: 0, height: 0 })
  }, [imageKey])

  const onImageLoad = useCallback((img: HTMLImageElement | null) => {
    if (!img) return
    const w = img.naturalWidth
    const h = img.naturalHeight
    if (w > 0 && h > 0) setNaturalSize({ width: w, height: h })
  }, [])

  const imageSize: Size =
    naturalSize.width > 0 && naturalSize.height > 0
      ? naturalSize
      : fallbackImage && fallbackImage.width > 0 && fallbackImage.height > 0
        ? fallbackImage
        : { width: 0, height: 0 }

  const fit = containFitRect(stageSize, imageSize)

  return { stageSize, imageSize, fit, onImageLoad }
}
