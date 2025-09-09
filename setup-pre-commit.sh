#!/bin/bash
# Setup script for pre-commit hooks
# Run this script after cloning the repository

set -e

echo "🔐 Setting up pre-commit hooks for Alliance AI Curation System..."

# Install pre-commit if not already installed
if ! command -v pre-commit &> /dev/null; then
    echo "📦 Installing pre-commit..."
    if command -v pipx &> /dev/null; then
        pipx install pre-commit
    elif command -v pip3 &> /dev/null; then
        pip3 install --user pre-commit
    else
        echo "❌ Error: Neither pipx nor pip3 found. Please install Python and pip first."
        exit 1
    fi
fi

# Install detect-secrets if not already installed
if ! command -v detect-secrets &> /dev/null; then
    echo "🔍 Installing detect-secrets..."
    if command -v pipx &> /dev/null; then
        pipx install detect-secrets
    elif command -v pip3 &> /dev/null; then
        pip3 install --user detect-secrets
    fi
fi

# Install pre-commit hooks
echo "🪝 Installing pre-commit hooks..."
pre-commit install

# Run hooks once to set up the environment
echo "🧪 Running pre-commit hooks for the first time..."
pre-commit run --all-files || {
    echo "⚠️  Some hooks failed on first run - this is normal for initial setup."
    echo "   The hooks are now installed and will run on future commits."
}

echo "✅ Pre-commit hooks setup complete!"
echo ""
echo "📋 Available commands:"
echo "  pre-commit run --all-files    # Run all hooks on all files"
echo "  pre-commit run detect-secrets # Run only secrets detection"
echo "  pre-commit autoupdate         # Update hook versions"
echo ""
echo "🛡️  Your repository is now protected against:"
echo "  • API keys and secrets"
echo "  • .env files"
echo "  • Large files"
echo "  • Syntax errors"
echo "  • Code formatting issues"
echo ""
echo "💡 The hooks will run automatically on every commit!"
