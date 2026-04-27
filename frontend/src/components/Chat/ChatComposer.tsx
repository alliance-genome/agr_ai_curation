import type { KeyboardEvent, ChangeEvent, RefObject } from 'react'

interface ChatComposerProps {
  textareaRef: RefObject<HTMLTextAreaElement>
  inputMessage: string
  isLoading: boolean
  onInputChange: (event: ChangeEvent<HTMLTextAreaElement>) => void
  onKeyPress: (event: KeyboardEvent<HTMLTextAreaElement>) => void
  onSendMessage: () => void
}

function ChatComposer({
  textareaRef,
  inputMessage,
  isLoading,
  onInputChange,
  onKeyPress,
  onSendMessage,
}: ChatComposerProps) {
  return (
    <div className="input-container">
      <textarea
        ref={textareaRef}
        className="message-input"
        placeholder="Type your message..."
        value={inputMessage}
        onChange={onInputChange}
        onKeyPress={onKeyPress}
        rows={1}
      />
      <button
        className="send-button"
        onClick={onSendMessage}
        disabled={isLoading || !inputMessage.trim()}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
          <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
        </svg>
      </button>
    </div>
  )
}

export default ChatComposer
