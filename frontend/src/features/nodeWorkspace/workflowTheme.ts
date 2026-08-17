export type WorkflowTheme = 'light' | 'dark'

export const WORKFLOW_THEME_KEY = 'bookcomet-workflow-theme'

export const CANVAS_DOT_COLOR: Record<WorkflowTheme, string> = {
  light: '#9ca3af',
  dark: '#2a2a2a',
}

export function readStoredWorkflowTheme(): WorkflowTheme {
  try {
    return localStorage.getItem(WORKFLOW_THEME_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}
