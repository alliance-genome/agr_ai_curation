import React, { useState, useEffect } from 'react'

/**
 * MaintenanceBanner Component
 *
 * Displays a toast notification in the bottom-left corner when a maintenance
 * message is configured in config/maintenance_message.txt.
 *
 * Features:
 * - Bottom-left positioned toast (doesn't interfere with navigation or chat)
 * - Dismissible with X button
 * - Fetches message on mount and every 5 minutes
 * - Yellow/orange theme matching site warnings
 */
const MaintenanceBanner: React.FC = () => {
  const [message, setMessage] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [dismissed, setDismissed] = useState(false)

  const fetchMaintenanceMessage = async () => {
    try {
      const response = await fetch('/api/maintenance/message')
      if (response.ok) {
        const data = await response.json()
        setMessage(data.active ? data.message : null)
      } else {
        setMessage(null)
      }
    } catch (error) {
      console.warn('Failed to fetch maintenance message:', error)
      setMessage(null)
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchMaintenanceMessage()
    const interval = setInterval(fetchMaintenanceMessage, 5 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  if (isLoading || !message || dismissed) {
    return null
  }

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 20,
        left: 20,
        zIndex: 10000,
        backgroundColor: '#ff9800',
        color: '#212121',
        padding: '1rem',
        borderRadius: '8px',
        boxShadow: '0 4px 12px rgba(0, 0, 0, 0.25)',
        maxWidth: '360px',
        border: '2px solid #f57c00',
      }}
    >
      {/* Close button */}
      <button
        onClick={() => setDismissed(true)}
        style={{
          position: 'absolute',
          top: 8,
          right: 8,
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          padding: 4,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: '4px',
          color: '#212121',
        }}
        aria-label="Dismiss"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
          <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
        </svg>
      </button>

      {/* Content */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem', paddingRight: '1.5rem' }}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" style={{ flexShrink: 0, marginTop: 2 }}>
          <path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z" />
        </svg>
        <div>
          <div style={{ fontWeight: 600, marginBottom: '0.25rem', fontSize: '0.95rem' }}>Scheduled Maintenance</div>
          <div style={{ fontSize: '0.875rem', lineHeight: 1.4 }}>{message}</div>
        </div>
      </div>
    </div>
  )
}

export default MaintenanceBanner
