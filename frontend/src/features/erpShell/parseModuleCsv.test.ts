import { describe, expect, it } from 'vitest'
import { parseModuleCsvTransactions } from './parseModuleCsv'

describe('parseModuleCsvTransactions', () => {
  it('parses AP sample headers', () => {
    const csv =
      'date,due_date,invoice_number,payee,amount,dr_cr,currency,tax_amount,account_code,category,memo,voucher_no\n' +
      '2026-01-10,2026-02-10,INV-1,Acme,1500.00,Dr,HKD,0,,Office,Memo,AP-001\n'
    const rows = parseModuleCsvTransactions(csv, 'AP')
    expect(rows).toHaveLength(1)
    expect(rows[0].payee).toBe('Acme')
    expect(rows[0].manual_entry).toBe(true)
    expect(Number(rows[0].amount)).toBe(1500)
  })

  it('parses AP export-style headers', () => {
    const csv =
      'ID No.,Invoice No.,Date,Due Date,Supplier,Debit,Credit,Tax,Cur,Account,Category,Payment\n' +
      'AP-1,INV-9,2025-01-02,2025-02-01,SAMPLEPAYEE,100,,0,HKD,5100,Office,\n'
    const rows = parseModuleCsvTransactions(csv, 'AP')
    expect(rows).toHaveLength(1)
    expect(rows[0].payee).toBe('SAMPLEPAYEE')
    expect(rows[0].debit).toBe(100)
  })

  it('parses Bank Amount column into deposit/withdrawal', () => {
    const csv =
      'Date,Description,Amount,Balance,Currency\n' +
      '2026-01-05,TRANSFER IN,5000.00,15000.00,HKD\n' +
      '2026-01-10,RENT,-3000.00,12000.00,HKD\n'
    const rows = parseModuleCsvTransactions(csv, 'BANK')
    expect(rows).toHaveLength(2)
    expect(rows[0].deposit).toBe(5000)
    expect(rows[1].withdrawal).toBe(3000)
  })
})
