/**
 * Feedback Service - API client for submitting curator feedback
 */

interface FeedbackSubmission {
  session_id: string
  curator_id: string
  feedback_text: string
  trace_ids: string[]
}

interface FeedbackResponse {
  status: 'success'
  feedback_id: string
  message: string
}

interface ErrorResponse {
  status: 'error'
  error: string
  details?: Array<{
    field: string
    message: string
  }>
}

/**
 * Submit curator feedback to the backend API
 *
 * @param feedback - The feedback submission data
 * @returns Promise with the API response
 * @throws Error if the submission fails
 */
export async function submitFeedback(feedback: FeedbackSubmission): Promise<FeedbackResponse> {
  try {
    const response = await fetch('/api/feedback/submit', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(feedback),
    })

    const data = await response.json()

    if (!response.ok) {
      // Handle error responses
      const errorData = data as ErrorResponse

      if (errorData.details && errorData.details.length > 0) {
        // Validation errors - combine detail messages
        const errorMessages = errorData.details.map(d => `${d.field}: ${d.message}`).join(', ')
        throw new Error(errorMessages)
      }

      // Generic error
      throw new Error(errorData.error || 'Failed to submit feedback')
    }

    return data as FeedbackResponse
  } catch (error) {
    // Network errors or errors thrown above
    if (error instanceof Error) {
      throw error
    }

    // Unknown error
    throw new Error('An unexpected error occurred while submitting feedback')
  }
}
