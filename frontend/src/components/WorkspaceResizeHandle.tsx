import { useCallback, useEffect, useRef, type ComponentProps } from 'react'
import { PanelResizeHandle } from 'react-resizable-panels'

/** Keep workspace drags attached to the handle, including across the PDF iframe. */
export default function WorkspaceResizeHandle(props: ComponentProps<typeof PanelResizeHandle>) {
  const dragging = useRef(false)
  const target = useRef<HTMLElement | null>(null)
  const stop = useCallback(() => {
    if (!dragging.current) return
    // react-resizable-panels 1.x listens for mouseup, but not pointer cancellation
    // or window blur. Use its existing termination path for interrupted drags.
    target.current?.ownerDocument.defaultView?.dispatchEvent(new MouseEvent('mouseup'))
  }, [])

  useEffect(() => {
    window.addEventListener('blur', stop)
    return () => {
      window.removeEventListener('blur', stop)
      dragging.current = false
    }
  }, [stop])

  return (
    <PanelResizeHandle
      {...props}
      onDragging={(active) => {
        dragging.current = active
        props.onDragging?.(active)
      }}
      onPointerDownCapture={(event) => {
        props.onPointerDownCapture?.(event)
        if (event.button !== 0 || !event.isPrimary || props.disabled) return
        // Version 1.x types currentTarget as a tag name rather than an element.
        const element = event.currentTarget as unknown as HTMLElement
        target.current = element
        element.setPointerCapture(event.pointerId)
      }}
      onPointerCancel={stop}
      onLostPointerCapture={stop}
      onPointerMoveCapture={(event) => {
        if (event.buttons === 0) stop()
      }}
    />
  )
}
