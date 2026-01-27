# Multi-stage Dockerfile for React/Vite frontend with Nginx

# Stage 1: Build the React app
FROM public.ecr.aws/docker/library/node:20-alpine AS builder

WORKDIR /app

# Build argument for DEV_MODE (Vite uses VITE_* prefix)
ARG VITE_DEV_MODE=true

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build the app for production with DEV_MODE environment variable
# Vite embeds VITE_* environment variables at build time
ENV VITE_DEV_MODE=$VITE_DEV_MODE
RUN npm run build

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