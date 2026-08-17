export type FooterStat = { label: string; value: string }

type Props = {
  selectedCount: number
  stats: FooterStat[]
}

export function GridFooter({ selectedCount, stats }: Props) {
  return (
    <div className="erp-footer">
      <span>Selected: <b>{selectedCount}</b></span>
      <span className="erp-spacer" />
      {stats.map(s => (
        <span key={s.label}>{s.label}: <b>{s.value}</b></span>
      ))}
    </div>
  )
}
