import { useCallback, useEffect, useRef, useState } from 'react'

export const LEFT_SIDEBAR_COLLAPSED_PX = 54

const LS_KEY = 'bookcomet-left-sidebar-collapsed'

export function useLeftSidebarCollapse() {
  const [leftPanelWidth, setLeftPanelWidth] = useState(260)
  const leftPanelWidthBeforeCollapseRef = useRef(260)
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(() => {
    try {
      return typeof localStorage !== 'undefined' && localStorage.getItem(LS_KEY) === '1'
    } catch {
      return false
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem(LS_KEY, leftSidebarCollapsed ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [leftSidebarCollapsed])

  const toggleLeftSidebarCollapsed = useCallback(() => {
    if (!leftSidebarCollapsed) {
      leftPanelWidthBeforeCollapseRef.current = leftPanelWidth
      setLeftSidebarCollapsed(true)
      return
    }
    setLeftPanelWidth(() =>
      Math.min(Math.max(leftPanelWidthBeforeCollapseRef.current, 160), 480),
    )
    setLeftSidebarCollapsed(false)
  }, [leftSidebarCollapsed, leftPanelWidth])

  return {
    leftPanelWidth,
    setLeftPanelWidth,
    leftSidebarCollapsed,
    toggleLeftSidebarCollapsed,
  }
}
