# Multi-stage Dockerfile for React/Vite frontend with Nginx

# Stage 1: Build the React app
FROM public.ecr.aws/docker/library/node:20-alpine AS builder

WORKDIR /app

# Build arguments for Vite environment variables (VITE_* prefix)
ARG VITE_DEV_MODE=true
ARG VITE_GIT_SHA=unknown

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build the app for production with environment variables
# Vite embeds VITE_* environment variables at build time
# Version is auto-extracted from package.json; SHA can be passed as build arg
ENV VITE_DEV_MODE=$VITE_DEV_MODE
ENV VITE_GIT_SHA=$VITE_GIT_SHA
RUN export VITE_APP_VERSION=$(node -p "require('./package.json').version") && \
    echo "Building version: $VITE_APP_VERSION (SHA: $VITE_GIT_SHA)" && \
    npm run build

# Stage 2: Serve with Nginx
FROM public.ecr.aws/nginx/nginx:alpine

# Remove default nginx website
RUN rm -rf /usr/share/nginx/html/*

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

# Start nginx
CMD ["nginx", "-g", "daemon off;"]