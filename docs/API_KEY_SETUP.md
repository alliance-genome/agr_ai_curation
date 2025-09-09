# AI Service API Key Configuration

This guide explains how to configure API keys for the AI chat integration feature.

## Required API Keys

The application supports two AI providers:

- **OpenAI** (GPT-4o, GPT-4o-mini, GPT-3.5-turbo)
- **Google Gemini** (Gemini 2.0 Flash, Gemini 1.5 Pro, Gemini 1.5 Flash)

You need at least one API key to use the AI chat feature.

## Getting API Keys

### OpenAI API Key

1. Go to [OpenAI Platform](https://platform.openai.com/)
2. Sign up or log in to your account
3. Navigate to API Keys section
4. Create a new API key
5. Copy the key (starts with `sk-`)

### Google Gemini API Key

1. Go to [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Sign in with your Google account
3. Click "Create API Key"
4. Copy the generated key

## Configuration Methods

### Method 1: Environment File (Recommended for Development)

Create a `.env` file in the project root:

```bash
# AI Service API Keys
OPENAI_API_KEY=sk-your-openai-api-key-here
GEMINI_API_KEY=your-gemini-api-key-here

# Optional: Set default provider (openai or gemini)
DEFAULT_AI_PROVIDER=openai
DEFAULT_AI_MODEL=gpt-4o
```

### Method 2: Docker Compose Environment

Add your API keys to the `docker-compose.yml` file:

```yaml
services:
  backend:
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - DEFAULT_AI_PROVIDER=openai
      - DEFAULT_AI_MODEL=gpt-4o
```

### Method 3: System Environment Variables

Export the variables in your shell:

```bash
export OPENAI_API_KEY="sk-your-openai-api-key-here"
export GEMINI_API_KEY="your-gemini-api-key-here"
```

## Security Best Practices

1. **Never commit API keys to version control**
   - Add `.env` to `.gitignore`
   - Use environment variables in production

2. **Use separate keys for development and production**
   - Create different API keys for each environment
   - Set usage limits on development keys

3. **Rotate keys regularly**
   - Replace API keys periodically
   - Revoke old keys after rotation

4. **Monitor usage**
   - Check API usage dashboards regularly
   - Set up billing alerts

## Troubleshooting

### Error: "AI service not configured"

- Ensure at least one API key is set
- Check that the key is valid and active
- Verify environment variables are loaded

### Error: "Rate limit exceeded"

- Wait for the rate limit window to reset
- Consider upgrading your API plan
- Implement request queuing in production

### Error: "Invalid API key"

- Double-check the key format
- Ensure no extra spaces or quotes
- Verify the key hasn't been revoked

## Testing Your Configuration

1. Start the application:

   ```bash
   docker compose up
   ```

2. Navigate to http://localhost:3000

3. Open the chat interface

4. Send a test message

5. Check the model selector dropdown - it should show available models

## API Usage and Costs

### OpenAI Pricing (as of 2024)

- GPT-4o: ~$5.00 / 1M input tokens, $15.00 / 1M output tokens
- GPT-4o-mini: ~$0.15 / 1M input tokens, $0.60 / 1M output tokens
- GPT-3.5-turbo: ~$0.50 / 1M input tokens, $1.50 / 1M output tokens

### Gemini Pricing (as of 2024)

- Gemini 2.0 Flash: Free tier available (15 RPM, 1M TPM)
- Gemini 1.5 Pro: ~$3.50 / 1M input tokens, $10.50 / 1M output tokens
- Gemini 1.5 Flash: ~$0.075 / 1M input tokens, $0.30 / 1M output tokens

## Rate Limiting

The application includes built-in rate limiting:

- Default: 60 requests per minute per IP
- Configurable in `backend/app/middleware/error_handler.py`

## Support

For issues with API key configuration:

1. Check the backend logs: `docker compose logs backend`
2. Verify environment variables: `docker compose exec backend env | grep API_KEY`
3. Test API keys directly using curl or Postman
