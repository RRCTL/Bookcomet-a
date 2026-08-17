import { describe, expect, it } from 'vitest'
import { coalesceBankAccountTypeRows } from './bankAccountTypeCoalesce'

describe('coalesceBankAccountTypeRows', () => {
  it('forward-fills section header account_type onto transaction rows', () => {
    const rows = coalesceBankAccountTypeRows([
      { 賬戶類型: 'HKD STATEMENT SAVINGS', 存入: '100' },
      { 存入: '50' },
      { 賬戶類型: 'HKD CURRENT', 提取: '10' },
      { 提取: '5' },
    ])
    expect(rows[0]?.account_type).toBe('HKD STATEMENT SAVINGS')
    expect(rows[1]?.account_type).toBe('HKD STATEMENT SAVINGS')
    expect(rows[2]?.account_type).toBe('HKD CURRENT')
    expect(rows[3]?.account_type).toBe('HKD CURRENT')
  })

  it('resets forward-fill when source_file stem changes', () => {
    const rows = coalesceBankAccountTypeRows([
      { source_file: 'HSBC-A.pdf P1', 賬戶類型: 'HKD STATEMENT SAVINGS', account_number: '111', 存入: '100' },
      { source_file: 'HSBC-A.pdf P2', 存入: '50' },
      { source_file: 'BOC-B.pdf P1', 賬戶類型: 'HKD STATEMENT SAVINGS', account_number: '222', 存入: '20' },
      { source_file: 'BOC-B.pdf P1', 存入: '10' },
    ])
    expect(rows[1]?.account_type).toBe('HKD STATEMENT SAVINGS')
    expect(rows[1]?.account_number).toBe('111')
    expect(rows[3]?.account_type).toBe('HKD STATEMENT SAVINGS')
    expect(rows[3]?.account_number).toBe('222')
  })

  it('forward-fills account_number within the same source file', () => {
    const rows = coalesceBankAccountTypeRows([
      { source_file: 'stmt.pdf P1', 賬戶類型: 'HKD CURRENT', account_number: '747-838', 存入: '1' },
      { source_file: 'stmt.pdf P1', 存入: '2' },
    ])
    expect(rows[1]?.account_number).toBe('747-838')
  })
})
