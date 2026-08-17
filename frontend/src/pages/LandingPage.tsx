import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { BookcometLogo } from '../components/BookcometLogo'
import './LandingPage.css'

function useInView(threshold = 0.15) {
  const ref = useRef<HTMLDivElement>(null)
  const [inView, setInView] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setInView(true)
      },
      { threshold },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [threshold])

  return { ref, inView }
}

const features = [
  {
    tag: 'CORE',
    title: 'Document Processing',
    desc: 'Upload PDFs and images for AP, AR, Bank, or Other. VLM/OCR extracts structured rows into review tables you can edit before approve.',
  },
  {
    tag: 'BOOKS',
    title: 'AP · AR · Bank books',
    desc: 'Mode-scoped grids with row edit, CSV export, approve flows, and Chart of Accounts deploy suggestions.',
  },
  {
    tag: 'MATCH',
    title: 'Reconciliation',
    desc: 'Match bank lines to ledger records, then draft GL journals from confirmed matches — with unmatched rows still editable.',
  },
  {
    tag: 'GL',
    title: 'General Ledger',
    desc: 'Review and manage journal entries produced from the capture and reconciliation workflow.',
  },
  {
    tag: 'AUTOMATION',
    title: 'Node workflows',
    desc: 'Canvas runs: import to VLM, merge and double-check, then table results — with skills, run history, and batch files.',
  },
  {
    tag: 'SETUP',
    title: 'Company setup',
    desc: 'Multi-company context, Chart of Accounts, rules and knowledge for the workspace.',
  },
]

const stats = [
  { value: '7', label: 'Live modules in books' },
  { value: '4', label: 'Capture modes (AP/AR/Bank/Other)' },
  { value: 'CSV', label: 'Import and export path' },
  { value: 'GL', label: 'Draft journals from matches' },
]

const workflowSteps = [
  {
    n: '01',
    title: 'Capture',
    desc: 'Pick AP, AR, Bank, or Other. Upload PDF, image, or CSV into Processing or a node workflow run.',
  },
  {
    n: '02',
    title: 'Review',
    desc: 'Edit extraction in the grid, deploy CoA codes, export CSV, and approve when the row set is ready.',
  },
  {
    n: '03',
    title: 'Reconcile and journal',
    desc: 'Match bank to books, draft GL entries, and keep humans in the loop before anything is treated as final.',
  },
]

const roadmap = [
  {
    label: 'Now',
    title: 'Core workspace',
    items: [
      'Processing with OCR/VLM',
      'AP, AR, and Bank books',
      'Reconciliation and GL journals',
      'Setup, CoA, multi-company',
      'Node workflow canvas and skills',
    ],
  },
  {
    label: 'Next',
    title: 'Books expansion',
    items: [
      'Other register module',
      'Financial Reports module',
      'Deeper recon lock/unmatch UX',
      'Richer reporting exports',
      'AI Account Agent develop',
    ],
  },
  {
    label: 'Later',
    title: 'Platform depth',
    items: [
      'Broader offline VLM packaging and install guides',
      'Compliance and audit trails',
      'More integrations and languages',
    ],
  },
]

function WorkflowCanvas() {
  const accent = '#2563eb'
  const soft = '#bfdbfe'
  const fill = '#e8f0fe'
  const ink = '#1e3a8a'

  const nodes = [
    { x: 16, y: 70, title: 'Upload', tag: 'INPUT', sub: 'PDF / image / CSV' },
    { x: 188, y: 30, title: 'VLM / OCR', tag: 'AI', sub: 'Extract rows' },
    { x: 370, y: 70, title: 'AP · AR · Bank', tag: 'BOOKS', sub: 'Review grids' },
    { x: 552, y: 30, title: 'Reconcile', tag: 'MATCH', sub: 'Bank to ledger' },
    { x: 734, y: 70, title: 'GL journal', tag: 'OUTPUT', sub: 'Draft entries' },
  ]

  return (
    <div className="lp-canvas" aria-hidden="true">
      <div className="lp-canvas-bar">
        <span className="lp-canvas-dots">
          <i />
          <i />
          <i />
        </span>
        <span className="lp-canvas-title">bookcomet — workflow.json</span>
        <span className="lp-canvas-status">saved</span>
      </div>
      <div className="lp-canvas-body">
        <svg viewBox="0 0 900 220" className="lp-canvas-svg">
          <defs>
            <marker id="lp-arrow" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" fill={accent} opacity="0.85" />
            </marker>
          </defs>
          <path d="M 148 110 C 168 110 168 70 188 70" stroke={accent} strokeWidth="2" fill="none" opacity="0.7" markerEnd="url(#lp-arrow)" />
          <path d="M 330 70 C 350 70 350 110 370 110" stroke={accent} strokeWidth="2" fill="none" opacity="0.7" markerEnd="url(#lp-arrow)" />
          <path d="M 512 110 C 532 110 532 70 552 70" stroke={accent} strokeWidth="2" fill="none" opacity="0.7" markerEnd="url(#lp-arrow)" />
          <path d="M 694 70 C 714 70 714 110 734 110" stroke={accent} strokeWidth="2" fill="none" opacity="0.7" markerEnd="url(#lp-arrow)" />
          {nodes.map((node) => (
            <g key={node.title}>
              <rect x={node.x} y={node.y} width="132" height="80" rx="10" fill="white" stroke={soft} strokeWidth="1.5" />
              <rect x={node.x} y={node.y} width="4" height="80" rx="2" fill={accent} />
              <text x={node.x + 16} y={node.y + 30} fontSize="12" fontWeight="700" fill={ink}>
                {node.title}
              </text>
              <rect x={node.x + 16} y={node.y + 38} width={node.tag.length * 7 + 10} height="14" rx="3" fill={fill} />
              <text x={node.x + 21} y={node.y + 48} fontSize="8" fill={accent} fontWeight="500">
                {node.tag}
              </text>
              <text x={node.x + 16} y={node.y + 68} fontSize="9" fill="#9ca3af">
                {node.sub}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <div className="lp-canvas-foot">
        <span className="lp-canvas-running">Running</span>
        <span>5 nodes · Processing to GL</span>
        <span className="lp-canvas-foot-end">Human review before post</span>
      </div>
    </div>
  )
}

export default function LandingPage() {
  const heroSection = useInView(0.1)
  const featuresSection = useInView(0.05)
  const statsSection = useInView(0.2)
  const workflowSection = useInView(0.1)
  const roadmapSection = useInView(0.1)

  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    document.body.style.overflow = menuOpen ? 'hidden' : ''
    return () => {
      document.body.style.overflow = ''
    }
  }, [menuOpen])

  const closeMenu = () => setMenuOpen(false)

  const navItems = [
    { href: '#features', label: 'Features' },
    { href: '#workflow', label: 'Workflow' },
    { href: '#modules', label: 'Modules' },
    { href: '#roadmap', label: 'Roadmap' },
  ]

  return (
    <div className={`lp-root${menuOpen ? ' lp-root--menu-open' : ''}`}>
      <header className={`lp-nav${scrolled || menuOpen ? ' lp-nav--scrolled' : ''}`}>
        <div className="lp-nav-inner">
          <Link to="/" className="lp-brand" aria-label="Bookcomet home" onClick={closeMenu}>
            <BookcometLogo variant="landing" alt="" />
            <span className="lp-brand-name">
              Bookcomet<span className="lp-brand-dot">.</span>
            </span>
          </Link>
          <nav className="lp-nav-links" aria-label="Primary">
            {navItems.map((item) => (
              <a key={item.href} href={item.href} className="lp-nav-link">
                {item.label}
              </a>
            ))}
          </nav>
          <div className="lp-nav-actions">
            <button
              type="button"
              className="lp-menu-toggle"
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={menuOpen}
              aria-controls="lp-mobile-menu"
              onClick={() => setMenuOpen((open) => !open)}
            >
              <span className={`lp-menu-toggle-bar${menuOpen ? ' is-open' : ''}`} />
            </button>
          </div>
        </div>
        <div
          id="lp-mobile-menu"
          className={`lp-mobile-menu${menuOpen ? ' is-open' : ''}`}
          hidden={!menuOpen}
        >
          <nav className="lp-mobile-nav" aria-label="Mobile">
            {navItems.map((item) => (
              <a key={item.href} href={item.href} className="lp-mobile-link" onClick={closeMenu}>
                {item.label}
              </a>
            ))}
          </nav>
        </div>
      </header>

      <section className="lp-hero">
        <div
          ref={heroSection.ref}
          className={`lp-hero-layout lp-fade-up${heroSection.inView ? ' lp-visible' : ''}`}
        >
          <div className="lp-hero-copy">
            <div className="lp-hero-badges">
              <span className="lp-chip lp-chip--accent">Open Source</span>
              <span className="lp-chip">AI Accounting</span>
              <span className="lp-chip">AP · AR · Bank · Recon · GL</span>
            </div>
            <h1 className="lp-hero-heading">
              Open-sourced accounting workflows that start with your{' '}
              <span className="lp-hero-accent">documents</span>.
            </h1>
            <p className="lp-hero-sub">
              Bookcomet is an open-source AI accounting workspace. Turn invoices, receipts, and bank
              statements into reviewable books — OCR/VLM extraction, AP/AR/Bank grids, reconciliation, and
              draft GL journals you can inspect, extend, and run yourself.
            </p>
            <ul className="lp-trust-list">
              <li>Open source — your code, your control</li>
              <li>Document to books</li>
              <li>Human review before post</li>
            </ul>
          </div>
          <div className="lp-hero-visual">
            <WorkflowCanvas />
          </div>
        </div>
      </section>

      <section className="lp-modules" id="modules">
        <div className="lp-section-header lp-section-header--left">
          <p className="lp-section-kicker">Modules</p>
          <h2 className="lp-section-title">What ships in the workspace today</h2>
          <p className="lp-section-sub">
            Live books modules in the workspace today — with Reports and Other register planned next.
          </p>
        </div>
        <div
          ref={statsSection.ref}
          className={`lp-stats-inner lp-fade-up${statsSection.inView ? ' lp-visible' : ''}`}
        >
          {stats.map((s) => (
            <div className="lp-stat" key={s.label}>
              <span className="lp-stat-value">{s.value}</span>
              <span className="lp-stat-label">{s.label}</span>
            </div>
          ))}
        </div>
        <ul className="lp-modules-list">
          <li>
            <strong>Processing</strong>
            <span>OCR &amp; VLM document capture</span>
          </li>
          <li>
            <strong>Accounts Payable</strong>
            <span>Invoice and payment review grids</span>
          </li>
          <li>
            <strong>Accounts Receivable</strong>
            <span>Receivables capture and approve</span>
          </li>
          <li>
            <strong>Bank</strong>
            <span>Bank statement extraction sheets</span>
          </li>
          <li>
            <strong>Reconciliation</strong>
            <span>Bank to ledger matching</span>
          </li>
          <li>
            <strong>General Ledger</strong>
            <span>Draft journals from the workflow</span>
          </li>
          <li>
            <strong>Setup</strong>
            <span>Company, CoA, and rules</span>
          </li>
        </ul>
        <p className="lp-stats-note">
          Enabled today: Processing, Accounts Payable, Accounts Receivable, Bank, Reconciliation, General
          Ledger, Setup. Reports and Other register are planned next.
        </p>
      </section>

      <section className="lp-features" id="features">
        <div className="lp-section-header lp-section-header--left">
          <p className="lp-section-kicker">Features</p>
          <h2 className="lp-section-title">Built around real Bookcomet modules</h2>
          <p className="lp-section-sub">
            From raw documents to reviewable books and draft journals — open-source automation with human
            control at every step.
          </p>
          <p className="lp-module-line">Processing · AP · AR · Bank · Recon · GL · Setup</p>
        </div>
        <div
          ref={featuresSection.ref}
          className={`lp-features-grid lp-fade-up${featuresSection.inView ? ' lp-visible' : ''}`}
        >
          {features.map((f) => (
            <div className="lp-feature-card" key={f.title}>
              <div className="lp-feature-card-top">
                <span className="lp-chip lp-chip--accent">{f.tag}</span>
              </div>
              <h3 className="lp-feature-title">{f.title}</h3>
              <p className="lp-feature-desc">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="lp-workflow" id="workflow">
        <div className="lp-section-header lp-section-header--left">
          <p className="lp-section-kicker">How it works</p>
          <h2 className="lp-section-title">Capture, review, then post with confidence</h2>
          <p className="lp-section-sub">
            Three steps from source documents to draft journals — the same path your workspace modules
            follow.
          </p>
        </div>
        <div
          ref={workflowSection.ref}
          className={`lp-workflow-grid lp-fade-up${workflowSection.inView ? ' lp-visible' : ''}`}
        >
          {workflowSteps.map((s) => (
            <div className="lp-workflow-card" key={s.n}>
              <span className="lp-workflow-n">{s.n}</span>
              <h3 className="lp-workflow-title">{s.title}</h3>
              <p className="lp-workflow-desc">{s.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="lp-roadmap" id="roadmap">
        <div className="lp-section-header lp-section-header--left">
          <p className="lp-section-kicker">Roadmap</p>
          <h2 className="lp-section-title">Honest product phases</h2>
          <p className="lp-section-sub">
            Aligned to what ships in Bookcomet today — not marketing placeholders.
          </p>
        </div>
        <div
          ref={roadmapSection.ref}
          className={`lp-roadmap-grid lp-fade-up${roadmapSection.inView ? ' lp-visible' : ''}`}
        >
          {roadmap.map((phase) => (
            <div className="lp-roadmap-card" key={phase.label}>
              <span className="lp-chip lp-chip--accent">{phase.label}</span>
              <h3 className="lp-roadmap-title">{phase.title}</h3>
              <ul className="lp-roadmap-list">
                {phase.items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      <section className="lp-cta-banner">
        <div className="lp-cta-banner-inner">
          <h2 className="lp-cta-banner-title">
            Open-source accounting — start with documents, finish with books you can trust.
          </h2>
          <p className="lp-cta-banner-sub">
            Bookcomet is an open-source AI accounting workspace for document capture, books, reconciliation,
            and draft journals. Inspect the stack, extend the workflows, and keep ownership of your books.
          </p>
        </div>
      </section>

      <footer className="lp-footer">
        <div className="lp-footer-inner">
          <div className="lp-footer-brand">
            <BookcometLogo variant="footer" alt="" />
            <span className="lp-brand-name">Bookcomet</span>
          </div>
          <div className="lp-footer-links">
            <a href="#features" className="lp-footer-link">
              Features
            </a>
            <a href="#workflow" className="lp-footer-link">
              Workflow
            </a>
            <a href="#modules" className="lp-footer-link">
              Modules
            </a>
            <a href="#roadmap" className="lp-footer-link">
              Roadmap
            </a>
            <a
              href="https://github.com/RRCTL/Bookcomet-a"
              className="lp-footer-link"
              rel="noopener noreferrer"
            >
              GitHub
            </a>
          </div>
          <p className="lp-footer-copy">
            © {new Date().getFullYear()} Bookcomet. Public MVP on{' '}
            <a href="https://github.com/RRCTL/Bookcomet-a" rel="noopener noreferrer">
              Bookcomet-a
            </a>
            . Cloud OCR/AI sends document and company profile data to the provider you configure.
          </p>
        </div>
      </footer>
    </div>
  )
}
