/**
 * Escape HTML special characters to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

class ChatInterface {
    constructor() {
        this.messagesContainer = document.getElementById('messagesContainer');
        this.messageInput = document.getElementById('messageInput');
        this.sendButton = document.getElementById('sendButton');
        this.messages = [];
        this.isLoading = false;
        this.sessionId = null;

        this.sessionInitPromise = this.initializeSession();
        this.initializeEventListeners();
    }

    async initializeSession() {
        // Create a new session when the page loads
        try {
            const response = await fetch('/api/chat/session', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            if (response.ok) {
                const data = await response.json();
                this.sessionId = data.session_id;
                console.log('Chat session initialized:', this.sessionId);
            } else {
                console.error('Failed to create session');
                // Generate a local UUID as fallback
                this.sessionId = this.generateUUID();
            }
        } catch (error) {
            console.error('Error creating session:', error);
            // Generate a local UUID as fallback
            this.sessionId = this.generateUUID();
        }

        return this.sessionId;
    }

    generateUUID() {
        // Simple UUID v4 generator
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            const r = Math.random() * 16 | 0;
            const v = c === 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    async ensureSession() {
        if (this.sessionId) {
            return this.sessionId;
        }

        if (!this.sessionInitPromise) {
            this.sessionInitPromise = this.initializeSession();
        }

        try {
            const result = await this.sessionInitPromise;
            if (result) {
                return result;
            }
        } catch (error) {
            console.error('Failed waiting for session initialization:', error);
        }

        if (!this.sessionId) {
            this.sessionId = this.generateUUID();
        }

        return this.sessionId;
    }

    initializeEventListeners() {
        this.sendButton.addEventListener('click', () => this.sendMessage());

        this.messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        this.messageInput.addEventListener('input', () => {
            this.adjustTextareaHeight();
        });
    }

    adjustTextareaHeight() {
        this.messageInput.style.height = 'auto';
        this.messageInput.style.height = Math.min(this.messageInput.scrollHeight, 120) + 'px';
    }

    addMessage(content, role, timestamp = new Date()) {
        const message = { content, role, timestamp };
        this.messages.push(message);
        this.renderMessages();
        this.scrollToBottom();
    }

    copyMessage(content) {
        navigator.clipboard.writeText(content).then(() => {
            console.log('Message copied to clipboard');
        }).catch(err => {
            console.error('Failed to copy message:', err);
        });
    }

    copyMessageFromButton(button) {
        // Safely retrieve content from data attribute (URL-encoded)
        const encodedContent = button.getAttribute('data-content');
        const content = decodeURIComponent(encodedContent);
        this.copyMessage(content);
    }

    renderMessages() {
        if (this.messages.length === 0 && !this.isLoading) {
            this.messagesContainer.innerHTML = '<div class="empty-state">Ask a question to get started...</div>';
            return;
        }

        const messagesHtml = this.messages.map(message => {
            const timeStr = message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            const isAssistant = message.role === 'assistant';
            const avatar = isAssistant ? '🤖' : '';
            const roleLabel = isAssistant ? 'Assistant' : 'You';

            // Escape content to prevent XSS
            const safeContent = escapeHtml(message.content);
            // For data attribute, encode to handle all special characters
            const encodedContent = encodeURIComponent(message.content);

            return `
                <div class="message ${message.role}">
                    <div class="message-header">
                        <div class="message-avatar">${avatar}</div>
                        <div class="message-content">
                            <div class="message-meta">${roleLabel} • ${timeStr}</div>
                            <div>${safeContent}</div>
                        </div>
                    </div>
                    <div class="message-actions">
                        <button class="copy-button" data-content="${encodedContent}" onclick="chatInterface.copyMessageFromButton(this)" title="Copy message">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/>
                            </svg>
                        </button>
                    </div>
                </div>
            `;
        }).join('');

        const loadingHtml = this.isLoading ? `
            <div class="loading">
                <span>AI is thinking<span class="loading-dots"></span></span>
            </div>
        ` : '';

        this.messagesContainer.innerHTML = messagesHtml + loadingHtml;
    }

    scrollToBottom() {
        setTimeout(() => {
            this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
        }, 10);
    }

    async sendMessage() {
        const content = this.messageInput.value.trim();
        if (!content || this.isLoading) return;

        await this.ensureSession();

        // Add user message
        this.addMessage(content, 'user');
        this.messageInput.value = '';
        this.adjustTextareaHeight();

        // Set loading state
        this.isLoading = true;
        this.sendButton.disabled = true;
        this.renderMessages();

        try {
            // Use AG-UI streaming protocol
            await this.sendStreamingMessage(content);
        } catch (error) {
            console.error('Error sending message:', error);
            this.addMessage('Sorry, I encountered an error. Please try again.', 'assistant');
        } finally {
            this.isLoading = false;
            this.sendButton.disabled = false;
            this.renderMessages();
        }
    }

    async sendStreamingMessage(content) {
        return new Promise((resolve, reject) => {
            fetch('/api/chat/stream', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream'
                },
                body: JSON.stringify({
                    message: content,
                    session_id: this.sessionId
                })
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let currentAssistantMessage = null;

                const processStream = () => {
                    return reader.read().then(({ done, value }) => {
                        if (done) {
                            console.log('AG-UI: Stream complete');
                            resolve();
                            return;
                        }

                        // Decode the chunk and add to buffer
                        buffer += decoder.decode(value, { stream: true });

                        // Process complete events (ending with \n\n)
                        let boundary = buffer.indexOf('\n\n');
                        while (boundary !== -1) {
                            const eventText = buffer.slice(0, boundary).trim();
                            buffer = buffer.slice(boundary + 2);

                            if (eventText.startsWith('data: ')) {
                                const dataText = eventText.slice(6);

                                try {
                                    const data = JSON.parse(dataText);
                                    console.log('AG-UI Event:', data);

                                    if (data.session_id && data.session_id !== this.sessionId) {
                                        this.sessionId = data.session_id;
                                    }

                                    switch (data.type) {
                                        case 'RUN_STARTED':
                                            console.log('AG-UI: Run started');
                                            break;

                                        case 'TEXT_MESSAGE_START':
                                            console.log('AG-UI: Message started');
                                            if (!currentAssistantMessage) {
                                                currentAssistantMessage = {
                                                    content: '',
                                                    role: 'assistant',
                                                    timestamp: new Date()
                                                };
                                                this.messages.push(currentAssistantMessage);
                                            }
                                            break;

                                        case 'TEXT_MESSAGE_CONTENT':
                                            if (!currentAssistantMessage) {
                                                currentAssistantMessage = {
                                                    content: '',
                                                    role: 'assistant',
                                                    timestamp: new Date()
                                                };
                                                this.messages.push(currentAssistantMessage);
                                            }

                                            if (data.delta) {
                                                currentAssistantMessage.content += data.delta;
                                                this.renderMessages();
                                                this.scrollToBottom();
                                            }
                                            break;

                                        case 'TEXT_MESSAGE_END':
                                            console.log('AG-UI: Message ended');
                                            break;

                                        case 'RUN_FINISHED':
                                            console.log('AG-UI: Run finished');
                                            resolve();
                                            return;

                                        case 'RUN_ERROR':
                                            console.error('AG-UI: Run error:', data.message);
                                            reject(new Error(data.message || 'AG-UI streaming error'));
                                            return;

                                        default:
                                            console.log('AG-UI: Unknown event type:', data.type);
                                    }
                                } catch (parseError) {
                                    console.error('Error parsing AG-UI event:', parseError, dataText);
                                }
                            }

                            boundary = buffer.indexOf('\n\n');
                        }

                        return processStream();
                    });
                };

                return processStream();
            })
            .catch(error => {
                console.error('AG-UI streaming error:', error);
                reject(error);
            });
        });
    }
}

// Initialize chat interface when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.chatInterface = new ChatInterface();
});
