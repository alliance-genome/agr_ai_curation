# How to Enable BuildKit Permanently

BuildKit provides better caching, faster builds, and advanced features. Here are several ways to enable it permanently:

## Method 1: Docker Daemon Configuration (Best - System-wide)

Edit Docker daemon config to enable BuildKit by default:

```bash
# Edit Docker daemon configuration
sudo nano /etc/docker/daemon.json
```

Add or update:

```json
{
  "features": {
    "buildkit": true
  }
}
```

Then restart Docker:

```bash
sudo systemctl restart docker
```

Now BuildKit is enabled for ALL Docker builds system-wide!

## Method 2: Shell Configuration (User-specific)

Add to your shell configuration file:

### For Bash (~/.bashrc or ~/.bash_profile):

```bash
echo 'export DOCKER_BUILDKIT=1' >> ~/.bashrc
echo 'export COMPOSE_DOCKER_CLI_BUILD=1' >> ~/.bashrc
source ~/.bashrc
```

### For Zsh (~/.zshrc):

```bash
echo 'export DOCKER_BUILDKIT=1' >> ~/.zshrc
echo 'export COMPOSE_DOCKER_CLI_BUILD=1' >> ~/.zshrc
source ~/.zshrc
```

### For Fish (~/.config/fish/config.fish):

```bash
echo 'set -x DOCKER_BUILDKIT 1' >> ~/.config/fish/config.fish
echo 'set -x COMPOSE_DOCKER_CLI_BUILD 1' >> ~/.config/fish/config.fish
source ~/.config/fish/config.fish
```

## Method 3: Docker Compose .env File (Project-specific)

Docker Compose automatically reads `.env` file in the same directory:

```bash
# Create .env file (already provided as .env.example)
cp .env.example .env

# Edit .env to add your API keys
nano .env
```

The `.env` file contains:

```env
DOCKER_BUILDKIT=1
COMPOSE_DOCKER_CLI_BUILD=1
```

Now just run:

```bash
docker compose build  # BuildKit enabled automatically!
```

## Method 4: Docker CLI Configuration (~/.docker/config.json)

Edit your Docker CLI config:

```bash
nano ~/.docker/config.json
```

Add:

```json
{
  "aliases": {
    "builder": "buildx"
  },
  "currentContext": "default"
}
```

Then use Docker Buildx (which always uses BuildKit):

```bash
# Install buildx if needed
docker buildx install

# Use buildx for builds
docker buildx build .
```

## Method 5: Use compose.yaml (Modern Compose Spec)

The newer `compose.yaml` filename (instead of `docker-compose.yml`) uses the Compose Specification which has better BuildKit integration:

```bash
# We've already created compose.yaml
docker compose build  # Uses compose.yaml automatically
```

## Verification

Check if BuildKit is enabled:

```bash
# Method 1: Check environment variable
echo $DOCKER_BUILDKIT

# Method 2: Run a build and look for BuildKit output
docker compose build backend 2>&1 | head -n 5
# Should show: [+] Building ... or #1 [internal] load build definition

# Method 3: Check Docker info
docker info | grep -i buildkit
```

## Quick Decision Guide

| Method        | Scope       | Permanent | Best For             |
| ------------- | ----------- | --------- | -------------------- |
| Daemon config | System-wide | ✅        | Production servers   |
| Shell config  | User        | ✅        | Development machines |
| .env file     | Project     | ✅        | Team projects        |
| CLI config    | User        | ✅        | Advanced users       |
| compose.yaml  | Project     | ✅        | Modern projects      |

## Recommended Setup for This Project

1. **For development**: Add to your shell config (Method 2)
2. **For the project**: Use the `.env` file (Method 3)
3. **For CI/CD**: Use daemon config or environment variables

## Simple One-Time Setup

```bash
# Quick setup for this project
cp .env.example .env
echo "✅ BuildKit will now be enabled for all docker compose commands!"
```

That's it! No more typing `DOCKER_BUILDKIT=1` every time!
