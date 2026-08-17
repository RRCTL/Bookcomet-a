import { useState } from 'react'
import './erp.css'
import { AppTopBar } from './AppTopBar'
import { ErpBackgroundJobsProvider } from './erpBackgroundJobs'
import { ModuleTree } from './ModuleTree'
import { ModuleTabBar } from './ModuleTabBar'
import { ProcessingPage } from './ProcessingPage'
import { ModuleGridPage } from './ModuleGridPage'
import { ReconPage } from './ReconPage'
import { JournalPage } from './JournalPage'
import { SetupPage } from './SetupPage'
import { ErpNavigationProvider } from './ErpNavigationContext'
import { DEFAULT_MODULE_ID, getModule, type ModuleDef } from './moduleRegistry'

function ModHead({ module }: { module: ModuleDef }) {
  return (
    <div className="erp-modhead">
      <span className="erp-title">{module.label}</span>
      <span className="erp-crumb">{module.crumb}</span>
    </div>
  )
}

function ModuleBody({ module }: { module: ModuleDef }) {
  if (module.kind === 'recon') return <ReconPage />
  if (module.kind === 'journal') return <JournalPage />
  if (module.kind === 'grid') return <ModuleGridPage module={module} />
  if (module.kind === 'setup') return <SetupPage />
  return null
}

export default function ErpShell() {
  const [openTabs, setOpenTabs] = useState<string[]>([DEFAULT_MODULE_ID])
  const [activeId, setActiveId] = useState<string>(DEFAULT_MODULE_ID)
  const [treeOpen, setTreeOpen] = useState(false)
  const [reconNavTick, setReconNavTick] = useState(0)

  const processingOpen = openTabs.includes('processing')

  const bumpReconLoad = () => setReconNavTick(t => t + 1)

  const select = (id: string) => {
    setOpenTabs(prev => (prev.includes(id) ? prev : [...prev, id]))
    setActiveId(id)
    setTreeOpen(false)
  }

  const close = (id: string) => {
    setOpenTabs(prev => {
      const next = prev.filter(t => t !== id)
      if (id === activeId) setActiveId(next[next.length - 1] ?? DEFAULT_MODULE_ID)
      return next.length ? next : [DEFAULT_MODULE_ID]
    })
  }

  const activeModule = getModule(activeId)

  return (
    <ErpBackgroundJobsProvider>
      <ErpNavigationProvider selectModule={select} reconNavTick={reconNavTick} bumpReconLoad={bumpReconLoad}>
      <div className="erp-shell">
        <AppTopBar onToggleMenu={() => setTreeOpen(o => !o)} />
      <ModuleTabBar openTabs={openTabs} activeId={activeId} onSelect={setActiveId} onClose={close} />
      <div className="erp-body">
        <ModuleTree activeId={activeId} open={treeOpen} onSelect={select} />
        <div className="erp-content">
          {/* Processing stays mounted to preserve node-workspace state across tab switches. */}
          {processingOpen && (
            <div
              style={{
                display: activeId === 'processing' ? 'flex' : 'none',
                flexDirection: 'column',
                flex: 1,
                minHeight: 0,
              }}
            >
              <ModHead module={getModule('processing')!} />
              <ProcessingPage />
            </div>
          )}
          {activeId !== 'processing' && activeModule && (
            <div className="erp-modbody">
              <ModHead module={activeModule} />
              <ModuleBody module={activeModule} />
            </div>
          )}
        </div>
      </div>
      </div>
      </ErpNavigationProvider>
    </ErpBackgroundJobsProvider>
  )
}
