# Security Guide

This document outlines the security measures implemented in the Alliance AI Curation System.

## 🔐 Pre-commit Hook Protection

We use multiple layers of protection to prevent sensitive data from being committed:

### Automated Security Checks

Every commit is automatically scanned for:

- **API Keys** - OpenAI, Anthropic, AWS, GitHub, and generic API keys
- **Secrets** - Passwords, tokens, connection strings, JWT tokens
- **Environment Files** - `.env` files are blocked (use `.env.example` instead)
- **Private Keys** - SSH keys, TLS certificates, and other cryptographic material
- **Large Files** - Files over 1MB are flagged
- **Database Dumps** - SQL dumps and database files are blocked

### Tools Used

1. **[detect-secrets](https://github.com/Yelp/detect-secrets)** - Comprehensive secret detection
2. **[gitleaks](https://github.com/gitleaks/gitleaks)** - Git-focused secret scanning
3. **[pre-commit](https://pre-commit.com/)** - Git hook framework
4. **Custom hooks** - Environment file protection and pattern detection

## 🚀 Quick Setup

Run this command after cloning the repository:

\`\`\`bash
./setup-pre-commit.sh
\`\`\`

Or manually:

\`\`\`bash

# Install pre-commit

pipx install pre-commit detect-secrets

# Install hooks

pre-commit install

# Test the setup

pre-commit run --all-files
\`\`\`

## 📁 File Protection

### Blocked Files

These file patterns are automatically blocked from commits:

- \`.env\` (use \`.env.example\` for templates)
- \`_.key\`, \`_.pem\`, \`\*.p12\` (private keys)
- \`id_rsa*\`, \`id_dsa*\` (SSH keys)
- \`credentials\*\` (credential files)
- \`_.sql\`, \`_.dump\` (database dumps)
- \`secrets.\*\` (except \`.secrets.baseline\`)

### Safe Files

These files are allowed and expected:

- \`.env.example\` (template for environment variables)
- \`.secrets.baseline\` (known safe secrets baseline)
- Documentation files (\`\*.md\`)
- Configuration files (with manual review)

## 🛠️ Manual Testing

### Test Secret Detection

\`\`\`bash

# Scan all files for secrets

detect-secrets scan .

# Run only secret detection hooks

pre-commit run detect-secrets --all-files
pre-commit run gitleaks --all-files
\`\`\`

### Test Environment Protection

\`\`\`bash

# This should be blocked

echo "API_KEY=sk-test123" > .env
git add .env
git commit -m "test" # Will fail!

# This is allowed

echo "API_KEY=your_key_here" > .env.example
git add .env.example
git commit -m "Add env template" # Will succeed
\`\`\`

## ⚙️ Configuration

### Baseline Management

The \`.secrets.baseline\` file contains known safe "secrets" (like example keys). To update it:

\`\`\`bash
detect-secrets scan . > .secrets.baseline
\`\`\`

### Custom Rules

Modify \`.gitleaks.toml\` to add custom secret patterns specific to your use case.

### Hook Configuration

Edit \`.pre-commit-config.yaml\` to:

- Add new hooks
- Modify existing hook behavior
- Update hook versions

## 🆘 Bypassing Hooks (Emergency Only)

In rare emergencies, you can bypass hooks:

\`\`\`bash

# Skip all hooks (DANGEROUS)

git commit --no-verify -m "Emergency commit"

# Skip specific hooks

SKIP=detect-secrets git commit -m "Skip only secrets check"
\`\`\`

**⚠️ Warning**: Only bypass hooks if you're certain no sensitive data is included!

## 🔄 Updating Hooks

Keep your security tools up to date:

\`\`\`bash

# Update all hook versions

pre-commit autoupdate

# Re-run setup after updates

pre-commit install
pre-commit run --all-files
\`\`\`

## 📞 Security Issues

If you discover a security vulnerability:

1. **DO NOT** commit the vulnerable code
2. Remove any sensitive data immediately
3. Review git history for accidentally committed secrets
4. Consider rotating any exposed credentials
5. Document the incident and prevention measures

## 🎯 Best Practices

### Environment Variables

✅ **Do This:**
\`\`\`bash

# .env.example (committed)

OPENAI_API_KEY=your_openai_key_here
DATABASE_URL=postgresql://user:pass@localhost:5432/db

# .env (NOT committed)

OPENAI_API_KEY=sk-real-key-here
DATABASE_URL=postgresql://realuser:realpass@localhost:5432/realdb
\`\`\`

❌ **Don't Do This:**
\`\`\`bash

# Hardcoded in source files

const API_KEY = "sk-real-key-here"; // NEVER!
\`\`\`

### API Keys

- Use environment variables for all secrets
- Rotate keys regularly
- Use least-privilege access
- Monitor usage for unauthorized access

### Development

- Run \`pre-commit run --all-files\` before important commits
- Review all warnings carefully
- When in doubt, ask for a security review
- Test changes in isolated environments first

---

This security setup provides multiple layers of protection, but remember: **security is everyone's responsibility!** 🛡️
