import { createContext, useContext, type ReactNode } from 'react'

type ErpNavigation = {
  selectModule: (id: string) => void
  /** Increments when Bank/AP/AR Reconcile is clicked (triggers scoped RECON load). */
  reconNavTick: number
  bumpReconLoad: () => void
}

const ErpNavigationContext = createContext<ErpNavigation>({
  selectModule: () => {},
  reconNavTick: 0,
  bumpReconLoad: () => {},
})

export function ErpNavigationProvider({
  selectModule,
  reconNavTick,
  bumpReconLoad,
  children,
}: {
  selectModule: (id: string) => void
  reconNavTick: number
  bumpReconLoad: () => void
  children: ReactNode
}) {
  return (
    <ErpNavigationContext.Provider value={{ selectModule, reconNavTick, bumpReconLoad }}>
      {children}
    </ErpNavigationContext.Provider>
  )
}

export function useErpNavigation(): ErpNavigation {
  return useContext(ErpNavigationContext)
}
