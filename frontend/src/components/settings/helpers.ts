import type { MemorySummary } from './types'

export const RULE_MEMORY_MODES = ['AR', 'AP', 'BANK', 'OTHER'] as const
export type RuleMemoryMode = (typeof RULE_MEMORY_MODES)[number]

export const RULE_MEMORY_MODE_LABELS: Record<RuleMemoryMode, string> = {
  AR: 'AR — Receivables',
  AP: 'AP — Payables',
  BANK: 'BANK — Bank Statements',
  OTHER: 'OTHER — Other',
}

export const SKILL_SLUG: Record<RuleMemoryMode, string> = {
  AR: 'ar-receivables',
  AP: 'ap-payables',
  BANK: 'bank-statements',
  OTHER: 'other',
}

export const KNOWLEDGE_NOTE_MAX = 2000

export function skillSkillFilename(mode: RuleMemoryMode): string {
  return `${SKILL_SLUG[mode]}.skill`
}

export function countRules(content: string): number {
  return content.split('\n').filter(l => {
    const t = l.trim()
    return t.startsWith('- ') && t.includes('→') && !t.startsWith('*(')
  }).length
}

export function detectConflictCount(content: string): number {
  const lines = content.split('\n').filter(l => {
    const t = l.trim()
    return t.startsWith('- ') && t.includes('→') && !t.startsWith('*(')
  })
  const vendors = new Map<string, number>()
  for (const line of lines) {
    const key = line.trim().slice(2).split('→')[0].trim().toLowerCase()
    vendors.set(key, (vendors.get(key) || 0) + 1)
  }
  return [...vendors.values()].filter(c => c > 1).length
}

export function formatRelativeTime(isoStr: string | null): string {
  if (!isoStr) return ''
  const diff = Date.now() - new Date(isoStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

export function ruleMemorySeenKey(companyId: string | null | undefined, mode: string): string {
  return `rm_seen_${companyId || 'default'}_${mode}`
}

export function isAINew(companyId: string | null | undefined, mode: string, summary?: MemorySummary): boolean {
  if (!summary || summary.updated_by_type !== 'ai') return false
  const lastSeen = localStorage.getItem(ruleMemorySeenKey(companyId, mode))
  if (!lastSeen || !summary.updated_at) return true
  return new Date(summary.updated_at) > new Date(lastSeen)
}

export function extractBehaviourPreview(md: string, maxLen = 110): string {
  const parts = md.split(/^##\s+AI Behaviour Instructions\s*$/im)
  if (parts.length < 2) return ''
  const body = (parts[1].split(/^##\s+/m)[0] || '').trim()
  const line = body.split('\n').find(l => l.trim().startsWith('- '))
  let s = line ? line.trim().replace(/^-\s+/, '').trim() : body.split('\n').find(l => l.trim())?.trim() || ''
  if (s.length > maxLen) s = `${s.slice(0, maxLen - 1)}…`
  return s
}
