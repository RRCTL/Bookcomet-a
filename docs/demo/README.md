# Fictional demo dataset

These files are the maintained **public** sample set for Bookcomet-a. They use obviously fictional names only:

- `Example Trading Limited`
- `Acme Supplies Ltd`
- `Sample Office Ltd`
- `Example Customer Ltd`
- `Sample Retail Ltd`
- `Sample Bank`

Do **not** replace them with real receipts, bank statements, customer lists, or lightly redacted originals.

| File | Use |
|---|---|
| [`ap-invoices.csv`](ap-invoices.csv) | AP import / Processing CSV |
| [`ar-invoices.csv`](ar-invoices.csv) | AR import |
| [`bank-statement.csv`](bank-statement.csv) | Bank CSV import |
| [`gl-draft.csv`](gl-draft.csv) | Example draft journal lines for review demos |

The same AP/AR/Bank rows are copied to `frontend/public/*-sample.csv` so the UI can offer them without a network fetch.

## How to try them

1. Complete the README quick start.
2. Create a company (use `Example Trading Limited`).
3. In Processing, import the CSV that matches the mode (AP, AR, or Bank).
4. Review the grid. Do not treat these rows as real books.
