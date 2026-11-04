import { describe, expect, it } from 'vitest'
import {
  applyPayloadsIfCurrentRun,
  clearReviewTableForRebuild,
  clearReviewTableOnRunSwitch,
  emptyReviewTableSession,
  editedRowsSafeForApprove,
  resolveDisplayRowsForRun,
  reviewTableShowingLoader,
} from './reviewTableSession'
import {
  RUN_ROW_FOREIGN_HIDDEN_NOTICE,
  filterPayloadRowsToRunFiles,
  partitionRowsByRunOwnership,
} from './runReviewRowOwnership'

const payloadsA = {
  'batch-a': {
    bankTransactions: [{ source_file: 'A.pdf P1', deposit: 13 }],
  },
}
const payloadsB = {
  'batch-b': {
    bankTransactions: [
      { source_file: 'Jan.pdf P1', deposit: 1 },
      { source_file: 'Jan.pdf P2', deposit: 2 },
    ],
  },
}

describe('reviewTableSession', () => {
  it('clears payloads and shows loader on run switch (no flash of prior rows)', () => {
    const prev = {
      boundRunId: 'run-a',
      payloads: payloadsA,
      editedRows: [{ source_file: 'A.pdf P1' }],
      loading: false,
    }
    const next = clearReviewTableOnRunSwitch('run-b')
    expect(next.loading).toBe(true)
    expect(next.boundRunId).toBeNull()
    expect(next.payloads).toEqual({})
    expect(next.editedRows).toBeNull()
    expect(reviewTableShowingLoader('run-b', next)).toBe(true)
    // Grid must render empty while loading — never prior run rows.
    expect(
      resolveDisplayRowsForRun(
        'run-b',
        next.boundRunId,
        (prev.payloads['batch-a']?.bankTransactions as unknown[]) ?? [],
        next.editedRows as never,
        next.loading,
      ),
    ).toEqual([])
    expect(prev.payloads).toEqual(payloadsA)
  })

  it('clears payloads and shows loader when rebuild from run files starts', () => {
    const prev = {
      boundRunId: 'run-a',
      payloads: payloadsA,
      editedRows: [{ source_file: 'Foreign.pdf P1', deposit: 99 }],
      loading: false,
    }
    const next = clearReviewTableForRebuild('run-a')
    expect(next.loading).toBe(true)
    expect(next.boundRunId).toBeNull()
    expect(next.payloads).toEqual({})
    expect(next.editedRows).toBeNull()
    expect(
      resolveDisplayRowsForRun(
        'run-a',
        next.boundRunId,
        (prev.payloads['batch-a']?.bankTransactions as unknown[]) ?? [],
        next.editedRows as never,
        next.loading,
      ),
    ).toEqual([])
  })

  it('ignores stale fetch results from a previously selected run', () => {
    let session = clearReviewTableOnRunSwitch('run-b')
    expect(reviewTableShowingLoader('run-b', session)).toBe(true)

    // Slow fetch for run-a arrives after user already selected run-b
    session = applyPayloadsIfCurrentRun(session, 'run-b', 'run-a', payloadsA)
    expect(session.payloads).toEqual({})
    expect(session.loading).toBe(true)

    session = applyPayloadsIfCurrentRun(session, 'run-b', 'run-b', payloadsB)
    expect(session.payloads).toEqual(payloadsB)
    expect(session.boundRunId).toBe('run-b')
    expect(session.loading).toBe(false)
    expect(reviewTableShowingLoader('run-b', session)).toBe(false)
  })

  it('does not keep edited overlay from another run when payloads arrive', () => {
    let session = {
      boundRunId: 'run-a',
      payloads: payloadsA,
      editedRows: [{ source_file: 'A.pdf P1', deposit: 13 }],
      loading: false,
    }
    session = clearReviewTableOnRunSwitch('run-b')
    session = {
      ...session,
      editedRows: [{ source_file: 'A.pdf P1', deposit: 13 }],
    }
    session = applyPayloadsIfCurrentRun(session, 'run-b', 'run-b', payloadsB)
    expect(session.editedRows).toBeNull()
    expect(session.payloads).toEqual(payloadsB)
  })

  it('resolveDisplayRowsForRun prefers edited rows only when bound to active run', () => {
    const canonical = [{ id: 'b1' }, { id: 'b2' }]
    const edited = [{ id: 'a1' }]
    expect(resolveDisplayRowsForRun('run-b', 'run-a', canonical, edited)).toEqual([])
    expect(resolveDisplayRowsForRun('run-b', 'run-b', canonical, edited)).toEqual(edited)
    expect(resolveDisplayRowsForRun('run-b', 'run-b', canonical, null)).toEqual(canonical)
    expect(resolveDisplayRowsForRun('run-b', 'run-b', canonical, edited, true)).toEqual([])
  })

  it('disables approve while loading or when overlay is from another run', () => {
    expect(editedRowsSafeForApprove('run-b', 'run-a', [{ x: 1 }])).toBe(false)
    expect(editedRowsSafeForApprove('run-b', 'run-b', [{ x: 1 }], true)).toBe(false)
    expect(editedRowsSafeForApprove('run-b', 'run-b', [{ x: 1 }])).toBe(true)
    expect(editedRowsSafeForApprove('run-b', 'run-b', null)).toBe(true)
  })

  it('shows foreign-hidden notice text and disables approve when rows reference foreign files', () => {
    const runBFiles = [
      { task_file_id: 'tf-b1', original_filename: 'Jan.pdf' },
      { task_file_id: 'tf-b2', original_filename: 'Feb.pdf' },
    ]
    const stickyFromA = [{ source_file: 'A.pdf P1', deposit: 13 }]
    const ownership = partitionRowsByRunOwnership(stickyFromA, runBFiles)
    expect(ownership.ok).toBe(false)
    expect(ownership.foreignCount).toBe(1)
    expect(ownership.ownedRows).toEqual([])
    expect(RUN_ROW_FOREIGN_HIDDEN_NOTICE).toBe(
      "Some rows came from files that aren't in this run and are hidden. Reload the run to fix this.",
    )
    const canApprove = ownership.ok && editedRowsSafeForApprove('run-b', 'run-b', stickyFromA)
    expect(canApprove).toBe(false)
  })

  it('restores correct rows after switch when session is cleared then hydrated', () => {
    let session = emptyReviewTableSession()
    session = applyPayloadsIfCurrentRun(session, 'run-a', 'run-a', payloadsA)
    expect(
      (session.payloads['batch-a']?.bankTransactions as { source_file: string }[])[0]?.source_file,
    ).toBe('A.pdf P1')

    session = clearReviewTableOnRunSwitch('run-b')
    expect(reviewTableShowingLoader('run-b', session)).toBe(true)
    expect(
      resolveDisplayRowsForRun('run-b', session.boundRunId, [{ id: 'stale' }], null, session.loading),
    ).toEqual([])

    session = applyPayloadsIfCurrentRun(session, 'run-b', 'run-b', payloadsB)
    const rows = session.payloads['batch-b']?.bankTransactions as { source_file: string }[]
    expect(rows).toHaveLength(2)
    expect(rows.every(r => r.source_file.startsWith('Jan.pdf'))).toBe(true)
  })

  it('approve/resume body built from filtered rows never includes foreign files', () => {
    const mixed = {
      bankTransactions: [
        { source_file: 'A.pdf P1', deposit: 13 },
        { source_file: 'Jan.pdf P1', deposit: 1 },
      ],
    }
    const runAFiles = [{ task_file_id: 'tf-a', original_filename: 'A.pdf' }]
    const { payload: body } = filterPayloadRowsToRunFiles(mixed, runAFiles, 'BANK')
    expect(body.bankTransactions).toEqual([{ source_file: 'A.pdf P1', deposit: 13 }])
  })
})
