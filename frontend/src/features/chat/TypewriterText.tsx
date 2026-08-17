import { useEffect, useRef, useState } from 'react'

export function TypewriterText({
  text,
  onComplete,
}: {
  text: string
  onComplete?: () => void
}) {
  const [charsShown, setCharsShown] = useState(0)
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete
  const completionFiredRef = useRef(false)

  useEffect(() => {
    setCharsShown(0)
    completionFiredRef.current = false
  }, [text])

  useEffect(() => {
    if (charsShown >= text.length) {
      if (!completionFiredRef.current && text.length > 0) {
        completionFiredRef.current = true
        onCompleteRef.current?.()
      }
      return
    }
    // Advance 4 chars per tick at 12 ms — feels like ~333 chars/sec
    const id = setTimeout(() => setCharsShown(n => Math.min(n + 4, text.length)), 12)
    return () => clearTimeout(id)
  }, [charsShown, text])

  const isDone = charsShown >= text.length
  return (
    <span>
      {text.slice(0, charsShown)}
      {!isDone && <span className="typewriter-cursor" aria-hidden="true">▋</span>}
    </span>
  )
}
