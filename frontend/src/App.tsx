/**
 * Re-export of the workspace shell. **Router entry:** `main.tsx` lazy-loads
 * `./features/workspace/WorkspaceApp` so `/` and auth pages do not pull this graph eagerly.
 */
export { default } from './features/workspace/WorkspaceApp'
