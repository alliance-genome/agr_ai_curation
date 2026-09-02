import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'

/**
 * Observe the rendered width of a container element.
 *
 * Returns null until the first measurement arrives, so callers can pick a
 * default layout before the element has a size.
 */
export function useContainerWidth<T extends HTMLElement>(): [RefObject<T>, number | null] {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState<number | null>(null)

  useEffect(() => {
    const element = ref.current
    if (!element) return undefined

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) {
        setWidth(entry.contentRect.width)
      }
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  return [ref, width]
}
