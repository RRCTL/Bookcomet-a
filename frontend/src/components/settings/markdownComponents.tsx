import { type ReactNode } from 'react'

export function renderInlineText(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/)
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4)
      return <strong key={i}>{part.slice(2, -2)}</strong>
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2 && !part.startsWith('**'))
      return <em key={i}>{part.slice(1, -1)}</em>
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2)
      return <code key={i} className="md-code">{part.slice(1, -1)}</code>
    return <span key={i}>{part}</span>
  })
}

function renderRuleLine(text: string): ReactNode {
  const arrowIdx = text.indexOf('→')
  if (arrowIdx !== -1) {
    const subject = text.slice(0, arrowIdx).trim()
    const mapping = text.slice(arrowIdx + 1).trim()
    return (
      <>
        <span className="rule-subject">{subject}</span>
        <span className="rule-arrow"> → </span>
        <span className="rule-mapping">{mapping}</span>
      </>
    )
  }
  if (text.startsWith('*(') || text.toLowerCase().startsWith('*format')) {
    return <span className="rule-comment">{text}</span>
  }
  return <>{renderInlineText(text)}</>
}

export function MarkdownRenderer({ content }: { content: string }) {
  if (!content.trim()) {
    return (
      <div className="md-empty">
        No rules yet. Click <strong>Edit</strong> to add rules or use <strong>AI Generate</strong> below.
      </div>
    )
  }
  const elements: ReactNode[] = []
  const lines = content.split('\n')
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()
    if (trimmed.startsWith('# ')) {
      elements.push(<h1 key={i} className="md-h1">{trimmed.slice(2)}</h1>)
    } else if (trimmed.startsWith('## ')) {
      elements.push(<h2 key={i} className="md-h2">{trimmed.slice(3)}</h2>)
    } else if (trimmed.startsWith('### ')) {
      elements.push(<h3 key={i} className="md-h3">{trimmed.slice(4)}</h3>)
    } else if (trimmed.startsWith('- ')) {
      const items: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('- ')) {
        items.push(lines[i].trim().slice(2))
        i++
      }
      elements.push(
        <ul key={`ul-${i}`} className="md-list">
          {items.map((item, j) => (
            <li key={j} className="md-li">{renderRuleLine(item)}</li>
          ))}
        </ul>
      )
      continue
    } else if (trimmed === '' || trimmed === '---') {
      // skip blank/hr lines
    } else {
      elements.push(<p key={i} className="md-p">{renderInlineText(trimmed)}</p>)
    }
    i++
  }
  return <div className="md-body">{elements}</div>
}

// ── Manual Sectioned View ─────────────────────────────────────────────────────

export function ManualSectionedView({ content }: { content: string }) {
  const sections: { heading: string; body: string; id: string }[] = []
  const lines = content.split('\n')
  let currentHeading = ''
  let currentId = ''
  let currentBody: string[] = []

  for (const line of lines) {
    const h2Match = line.match(/^##\s+(.+)/)
    if (h2Match) {
      if (currentHeading) {
        sections.push({ heading: currentHeading, id: currentId, body: currentBody.join('\n').trim() })
      }
      currentHeading = h2Match[1].trim()
      currentId = `manual-section-${currentHeading.replace(/\s+/g, '-')}`
      currentBody = []
    } else if (!currentHeading && line.startsWith('# ')) {
      // skip title line
    } else if (currentHeading) {
      currentBody.push(line)
    }
  }
  if (currentHeading) {
    sections.push({ heading: currentHeading, id: currentId, body: currentBody.join('\n').trim() })
  }

  if (sections.length === 0) {
    return <div className="md-body"><MarkdownRenderer content={content} /></div>
  }

  return (
    <div className="manual-sections">
      {sections.map(s => (
        <div key={s.id} id={s.id} className="manual-section-block">
          <div className="manual-section-heading">{s.heading}</div>
          {s.body ? (
            <MarkdownRenderer content={s.body} />
          ) : (
            <div className="manual-section-empty">No content yet — click to edit.</div>
          )}
        </div>
      ))}
    </div>
  )
}
