-- Migration: Relax PDF viewer unique constraints to allow duplicate source files
-- Created: 2025-09-28

ALTER TABLE pdf_documents
    DROP CONSTRAINT IF EXISTS uq_pdf_documents_file_path,
    DROP CONSTRAINT IF EXISTS uq_pdf_documents_file_hash;
