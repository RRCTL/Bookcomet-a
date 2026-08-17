import '../erpShell/erp.css'
import { ModuleGridPage } from '../erpShell/ModuleGridPage'
import { getModule } from '../erpShell/moduleRegistry'

type Props = {
  moduleId: 'ar' | 'ap'
}

/** Flat module grid (same as ERP shell) embedded in legacy WorkspaceApp. */
export function WorkspaceModuleGridPanel({ moduleId }: Props) {
  const module = getModule(moduleId)
  if (!module) return null

  return (
    <div className="workspace-module-grid-host">
      <ModuleGridPage module={module} />
    </div>
  )
}
