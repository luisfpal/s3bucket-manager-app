/**
 * Auto-dismissing message hooks.
 * Errors auto-clear after 6 s; success banners after 3 s.
 * Clicking the message dismisses it immediately (call setError(null)).
 * A new message resets the timer.
 */

import { useState, useEffect, useRef, useCallback } from 'react'

export function useAutoError(timeoutMs = 6000) {
  const [error, setErrorRaw] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const setError = useCallback((msg: string | null) => {
    if (timer.current) clearTimeout(timer.current)
    setErrorRaw(msg)
    if (msg) timer.current = setTimeout(() => setErrorRaw(null), timeoutMs)
  }, [timeoutMs])

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  return [error, setError] as const
}

export function useAutoSuccess(timeoutMs = 3000) {
  const [success, setSuccessRaw] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const setSuccess = useCallback((msg: string | null) => {
    if (timer.current) clearTimeout(timer.current)
    setSuccessRaw(msg)
    if (msg) timer.current = setTimeout(() => setSuccessRaw(null), timeoutMs)
  }, [timeoutMs])

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  return [success, setSuccess] as const
}
