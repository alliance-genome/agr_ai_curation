#!/usr/bin/env python3
"""
Cleanup Orphaned PDF Records

This script scans the PostgreSQL database for PDF records that do not have a corresponding
entry in Weaviate. These "phantom" records prevent users from re-uploading files.

Usage:
    python3 scripts/cleanup_orphaned_pdf_records.py [--no-dry-run]
"""

import argparse
import asyncio
import logging
import sys
import os
from pathlib import Path
import shutil

# Add backend to path to allow imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from src.lib.weaviate_client.documents import get_document
from src.config import get_pdf_storage_path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def check_and_cleanup(dry_run: bool):
    logger.info(f"Starting cleanup scan (Dry Run: {dry_run})")
    
    session = SessionLocal()
    try:
        # Fetch all documents with user info
        # PDFDocument has user_id (int). User table matches user_id.
        stmt = select(PDFDocument, User).join(User, PDFDocument.user_id == User.user_id, isouter=True)
        
        result = session.execute(stmt)
        rows = result.all()
        
        logger.info(f"Found {len(rows)} documents in PostgreSQL.")
        
        orphans_found = 0
        orphans_deleted = 0
        
        for doc, user in rows:
            if not user:
                logger.warning(f"Document {doc.id} has no associated user (user_id={doc.user_id}). Skipping Weaviate check (unknown tenant).")
                continue
                
            tenant_id = user.user_id
            
            try:
                # Check Weaviate
                weaviate_doc = await get_document(tenant_id, str(doc.id))
                
                if not weaviate_doc:
                    orphans_found += 1
                    logger.warning(f"ORPHAN DETECTED: Document {doc.id} ({doc.filename}) for user {user.email} (Tenant: {tenant_id}) is missing in Weaviate.")
                    
                    if not dry_run:
                        # DELETE logic
                        logger.info(f"Deleting orphaned record {doc.id}...")
                        
                        # 1. Delete physical files
                        try:
                            base_storage = get_pdf_storage_path()
                            if doc.file_path:
                                file_path_obj = Path(base_storage) / doc.file_path
                                doc_dir = file_path_obj.parent
                                if doc_dir.exists() and Path(base_storage) in doc_dir.parents:
                                    shutil.rmtree(doc_dir)
                                    logger.info(f"  - Deleted filesystem directory: {doc_dir}")
                        except Exception as e:
                            logger.error(f"  - Failed to delete files: {e}")
                            
                        # 2. Delete DB record
                        session.delete(doc)
                        session.commit()
                        orphans_deleted += 1
                        logger.info("  - Deleted PostgreSQL record.")
                        
            except Exception as e:
                logger.error(f"Error checking document {doc.id}: {e}")
                
        logger.info("-" * 40)
        logger.info(f"Scan Complete.")
        logger.info(f"Total Documents Scanned: {len(rows)}")
        logger.info(f"Orphans Found: {orphans_found}")
        if not dry_run:
            logger.info(f"Orphans Deleted: {orphans_deleted}")
        else:
            logger.info("Run with --no-dry-run to perform deletion.")

    finally:
        session.close()

def main():
    parser = argparse.ArgumentParser(description='Cleanup orphaned PDF records.')
    parser.add_argument('--no-dry-run', action='store_true', help='Execute deletion (default is dry-run)')
    args = parser.parse_args()
    
    dry_run = not args.no_dry_run
    
    asyncio.run(check_and_cleanup(dry_run))

if __name__ == "__main__":
    main()
