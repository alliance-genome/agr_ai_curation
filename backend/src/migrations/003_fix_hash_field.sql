-- Migration: Fix file_hash field length for SHA-256 hashes
-- Created: 2025-09-28

ALTER TABLE pdf_documents
ALTER COLUMN file_hash TYPE VARCHAR(64);