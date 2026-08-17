import { useRef } from 'react'

/**
 * Refs for upload / RECON / REPORT navigation (timer reset flags).
 * OCR→RECON and RECON→REPORT 15-minute nudges are disabled for the current product stage.
 */
export function useStageProgressionReminders() {
  const lastOcrUploadTimeRef = useRef<number>(Date.now())
  const ocrStageReminderSentRef = useRef<boolean>(false)
  const lastReconActivityTimeRef = useRef<number>(Date.now())
  const reconStageReminderSentRef = useRef<boolean>(false)

  return {
    lastOcrUploadTimeRef,
    ocrStageReminderSentRef,
    lastReconActivityTimeRef,
    reconStageReminderSentRef,
  }
}
