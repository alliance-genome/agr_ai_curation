-- Migration: Add file path columns for storing intermediate processing files
-- Created: 2025-09-28

ALTER TABLE pdf_documents
ADD COLUMN IF NOT EXISTS docling_json_path VARCHAR(500),
ADD COLUMN IF NOT EXISTS processed_json_path VARCHAR(500);

-- Create indexes for faster lookups
CREATE INDEX IF NOT EXISTS idx_pdf_documents_docling_json_path ON pdf_documents(docling_json_path);
CREATE INDEX IF NOT EXISTS idx_pdf_documents_processed_json_path ON pdf_documents(processed_json_path);