import { CLOUD_AI_DATA_NOTICE } from '../constants/privacyNotices'
import './CloudAiNotice.css'

type Props = {
  className?: string
}

export function CloudAiNotice({ className }: Props) {
  return (
    <p className={className ? `cloud-ai-notice ${className}` : 'cloud-ai-notice'} role="note">
      {CLOUD_AI_DATA_NOTICE}
    </p>
  )
}
