# Multi-stage Dockerfile for React/Vite frontend with Nginx

# Stage 1: Build the React app
FROM public.ecr.aws/docker/library/node:20-alpine AS builder

WORKDIR /app

# Build arguments for Vite environment variables (VITE_* prefix)
ARG VITE_DEV_MODE=true
ARG VITE_GIT_SHA=unknown
ARG VITE_RUN_COMPLETION_TOAST_AUTO_HIDE_MS=6000
ARG VITE_AI_CURATION_DRAFT_AUTOSAVE_MAX_ATTEMPTS=2
ARG VITE_AI_CURATION_DRAFT_AUTOSAVE_RETRY_DELAY_MS=0
ARG VITE_ADD_LITERATURE_MAX_SELECTED_FILES=10
ARG VITE_ADD_LITERATURE_PDF_JOB_WINDOW_DAYS=7
ARG VITE_ADD_LITERATURE_PDF_JOB_LIMIT=50
ARG VITE_ADD_LITERATURE_FALLBACK_POLL_INTERVAL_MS=5000

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
ENV VITE_RUN_COMPLETION_TOAST_AUTO_HIDE_MS=$VITE_RUN_COMPLETION_TOAST_AUTO_HIDE_MS
ENV VITE_AI_CURATION_DRAFT_AUTOSAVE_MAX_ATTEMPTS=$VITE_AI_CURATION_DRAFT_AUTOSAVE_MAX_ATTEMPTS
ENV VITE_AI_CURATION_DRAFT_AUTOSAVE_RETRY_DELAY_MS=$VITE_AI_CURATION_DRAFT_AUTOSAVE_RETRY_DELAY_MS
ENV VITE_ADD_LITERATURE_MAX_SELECTED_FILES=$VITE_ADD_LITERATURE_MAX_SELECTED_FILES
ENV VITE_ADD_LITERATURE_PDF_JOB_WINDOW_DAYS=$VITE_ADD_LITERATURE_PDF_JOB_WINDOW_DAYS
ENV VITE_ADD_LITERATURE_PDF_JOB_LIMIT=$VITE_ADD_LITERATURE_PDF_JOB_LIMIT
ENV VITE_ADD_LITERATURE_FALLBACK_POLL_INTERVAL_MS=$VITE_ADD_LITERATURE_FALLBACK_POLL_INTERVAL_MS
RUN export VITE_APP_VERSION=$(node -p "require('./package.json').version") && \
    echo "Building version: $VITE_APP_VERSION (SHA: $VITE_GIT_SHA)" && \
    npm run build && \
    node -e 'const fs = require("fs"); const raw = process.env.VITE_DEV_MODE; if (raw !== "true" && raw !== "false") throw new Error("VITE_DEV_MODE must be true or false"); fs.writeFileSync("dist/build-metadata.json", JSON.stringify({ schema_version: 1, vite_dev_mode: raw === "true", git_sha: process.env.VITE_GIT_SHA }) + "\n")'

# Stage 2: Configure the Nginx runtime independently so the rendered proxy
# contract can be tested without rebuilding the frontend assets.
FROM public.ecr.aws/nginx/nginx:alpine AS nginx-runtime-base

ENV PDF_MAX_FILE_SIZE_BYTES=524288000
ENV NGINX_ENVSUBST_FILTER="^PDF_MAX_FILE_SIZE_BYTES$"

# Render the runtime limit through the official Nginx entrypoint after first
# validating that the value is a positive byte count.
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.d/05-validate-pdf-limit.sh /docker-entrypoint.d/05-validate-pdf-limit.sh
RUN chmod 755 /docker-entrypoint.d/05-validate-pdf-limit.sh

# Stage 3: Serve the built application with the configured Nginx runtime.
FROM nginx-runtime-base

# Remove default nginx website
RUN rm -rf /usr/share/nginx/html/*

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Expose port 80
EXPOSE 80

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
