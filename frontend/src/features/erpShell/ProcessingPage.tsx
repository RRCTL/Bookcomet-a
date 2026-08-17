import { ProcessingView } from './ProcessingView'

/**
 * Processing module = ERP-native, canvas-forward node view (ProcessingView).
 * The legacy NodeWorkspace/OpenWebUI shell is intentionally NOT embedded here;
 * it remains the VITE_UI_THEME=legacy rollback target and is untouched.
 */
export function ProcessingPage() {
  return <ProcessingView />
}
