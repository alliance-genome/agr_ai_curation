import { useEffect, useState } from 'react'

function ScrollDebug() {
  const [scrollInfo, setScrollInfo] = useState<{
    body: { scrollHeight: number; clientHeight: number; scrollTop: number }
    messages: { scrollHeight: number; clientHeight: number; scrollTop: number } | null
  }>({
    body: { scrollHeight: 0, clientHeight: 0, scrollTop: 0 },
    messages: null
  })

  useEffect(() => {
    const updateInfo = () => {
      const messagesContainer = document.querySelector('[data-testid="messages-container"]') ||
                               document.querySelector('.messages-container') ||
                               Array.from(document.querySelectorAll('div')).find(el =>
                                 el.style.overflowY === 'auto' && el.style.flex === '1'
                               )

      setScrollInfo({
        body: {
          scrollHeight: document.body.scrollHeight,
          clientHeight: document.body.clientHeight,
          scrollTop: document.body.scrollTop || document.documentElement.scrollTop
        },
        messages: messagesContainer ? {
          scrollHeight: messagesContainer.scrollHeight,
          clientHeight: messagesContainer.clientHeight,
          scrollTop: messagesContainer.scrollTop
        } : null
      })
    }

    updateInfo()
    window.addEventListener('scroll', updateInfo, true)
    const interval = setInterval(updateInfo, 1000)

    return () => {
      window.removeEventListener('scroll', updateInfo, true)
      clearInterval(interval)
    }
  }, [])

  return (
    <div style={{
      position: 'fixed',
      bottom: 10,
      right: 10,
      background: 'rgba(0,0,0,0.8)',
      color: '#0f0',
      padding: '10px',
      fontSize: '12px',
      fontFamily: 'monospace',
      zIndex: 9999,
      borderRadius: '5px',
      maxWidth: '300px'
    }}>
      <div><b>Body:</b></div>
      <div>scrollHeight: {scrollInfo.body.scrollHeight}</div>
      <div>clientHeight: {scrollInfo.body.clientHeight}</div>
      <div>scrollTop: {scrollInfo.body.scrollTop}</div>
      <div>scrollable: {scrollInfo.body.scrollHeight > scrollInfo.body.clientHeight ? 'YES ❌' : 'NO ✅'}</div>

      {scrollInfo.messages && (
        <>
          <div style={{ marginTop: '10px' }}><b>Messages Container:</b></div>
          <div>scrollHeight: {scrollInfo.messages.scrollHeight}</div>
          <div>clientHeight: {scrollInfo.messages.clientHeight}</div>
          <div>scrollTop: {scrollInfo.messages.scrollTop}</div>
          <div>scrollable: {scrollInfo.messages.scrollHeight > scrollInfo.messages.clientHeight ? 'YES ✅' : 'NO ❌'}</div>
        </>
      )}
    </div>
  )
}

export default ScrollDebug