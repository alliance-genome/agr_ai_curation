FROM postgres:16-alpine

# Install build dependencies for pgvector
RUN apk add --no-cache --virtual .build-deps \
    git \
    make \
    gcc \
    g++ \
    clang15 \
    llvm15-dev

# Install pgvector extension
RUN cd /tmp && \
    git clone --branch v0.7.0 https://github.com/pgvector/pgvector.git && \
    cd pgvector && \
    make && \
    make install && \
    cd .. && \
    rm -rf pgvector

# Clean up build dependencies
RUN apk del .build-deps

# Copy initialization scripts
COPY ./docker/postgres/init-pgvector.sql /docker-entrypoint-initdb.d/01-init-pgvector.sql
COPY ./docker/init.sql /docker-entrypoint-initdb.d/02-init.sql

# Ensure postgres user can access everything
USER postgres
