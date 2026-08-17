import { useSyncExternalStore } from 'react'

const MOBILE_QUERY = '(max-width: 639px)'
const TABLET_QUERY = '(min-width: 640px) and (max-width: 1023px)'
const DESKTOP_QUERY = '(min-width: 1024px)'

function createMediaSubscription(query: string) {
  const mql = typeof window !== 'undefined' ? window.matchMedia(query) : null

  function subscribe(callback: () => void) {
    mql?.addEventListener('change', callback)
    return () => mql?.removeEventListener('change', callback)
  }

  function getSnapshot() {
    return mql?.matches ?? false
  }

  return { subscribe, getSnapshot }
}

const mobile = createMediaSubscription(MOBILE_QUERY)
const tablet = createMediaSubscription(TABLET_QUERY)
const desktop = createMediaSubscription(DESKTOP_QUERY)

export function useViewport() {
  const isMobile = useSyncExternalStore(mobile.subscribe, mobile.getSnapshot, () => false)
  const isTablet = useSyncExternalStore(tablet.subscribe, tablet.getSnapshot, () => false)
  const isDesktop = useSyncExternalStore(desktop.subscribe, desktop.getSnapshot, () => true)

  return { isMobile, isTablet, isDesktop }
}
