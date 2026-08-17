import { useMemo, useState } from 'react'
import { TypewriterText } from './TypewriterText'

/** Rotating status + typewriter while the background AI job is in flight. */
export function AiChatThinkingIndicator() {
  const phrases = useMemo(
    () => [
      'Connecting to AI…',
      'Preparing your reply…',
      'This may take a few seconds — thanks for waiting…',
    ],
    [],
  )
  const [phraseIndex, setPhraseIndex] = useState(0)

  return (
    <div className="ai-chat-thinking" aria-live="polite" aria-busy="true">
      <div className="ai-chat-thinking-row">
        <span className="ai-chat-thinking-label">Thinking</span>
        <span className="ai-chat-thinking-dots" aria-hidden="true">
          <span className="ai-chat-thinking-dot" />
          <span className="ai-chat-thinking-dot" />
          <span className="ai-chat-thinking-dot" />
        </span>
      </div>
      <div className="ai-chat-thinking-typewriter">
        <TypewriterText
          key={phraseIndex}
          text={phrases[phraseIndex]!}
          onComplete={() => {
            window.setTimeout(() => {
              setPhraseIndex(i => (i + 1) % phrases.length)
            }, 900)
          }}
        />
      </div>
    </div>
  )
}
