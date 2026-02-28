-- Migration: Add file path columns for storing intermediate processing files
-- Created: 2025-09-28

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'pdf_documents'
          AND column_name = 'docling_json_path'
    )
    AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'pdf_documents'
          AND column_name = 'pdfx_json_path'
    ) THEN
        ALTER TABLE pdf_documents
        RENAME COLUMN docling_json_path TO pdfx_json_path;
    END IF;
END $$;

ALTER TABLE pdf_documents
ADD COLUMN IF NOT EXISTS pdfx_json_path VARCHAR(500),
ADD COLUMN IF NOT EXISTS processed_json_path VARCHAR(500);

-- Create indexes for faster lookups
DROP INDEX IF EXISTS idx_pdf_documents_docling_json_path;
CREATE INDEX IF NOT EXISTS idx_pdf_documents_pdfx_json_path ON pdf_documents(pdfx_json_path);
CREATE INDEX IF NOT EXISTS idx_pdf_documents_processed_json_path ON pdf_documents(processed_json_path);
