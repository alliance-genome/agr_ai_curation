# Build stage
FROM node:20-alpine AS builder
WORKDIR /app

# Accept NODE_ENV as build argument (defaults to development for local builds)
ARG NODE_ENV=development
ENV NODE_ENV=${NODE_ENV}

COPY package*.json ./
# Build with the committed lockfile and keep devDependencies available for vite/tsc.
RUN npm ci --include=dev
COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine
RUN apk add --no-cache gettext
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV TRACE_REVIEW_PUBLIC_API_URL=http://host.docker.internal:8001
EXPOSE 80
CMD ["/bin/sh", "-c", "envsubst '$TRACE_REVIEW_PUBLIC_API_URL' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && exec nginx -g 'daemon off;'"]
