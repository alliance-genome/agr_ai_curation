"""Shared UUID fixtures for contract tests.

This module provides valid UUIDs to replace the invalid numeric IDs
and string IDs that were causing UUID validation errors in tests.

All UUIDs are generated once and reused across tests for consistency.
"""

import uuid

# Valid UUIDs for test documents
TEST_DOC_UUID_1 = "f35596eb-618d-4904-822f-a15eacc5ec94"
TEST_DOC_UUID_2 = "27a7287b-9a51-4bf3-906d-ec6f1a5d59d1"
TEST_DOC_UUID_3 = "3f54b25b-8122-4aec-809e-31562f7141bd"
TEST_DOC_UUID_4 = "aa25deb6-0dfe-4bb1-8221-f320106c48b1"
TEST_DOC_UUID_NONEXISTENT = "925e41a8-c4e4-484b-92eb-4028d8543623"  # For 404 tests

# Verify all UUIDs are valid
for test_uuid in [TEST_DOC_UUID_1, TEST_DOC_UUID_2, TEST_DOC_UUID_3,
                   TEST_DOC_UUID_4, TEST_DOC_UUID_NONEXISTENT]:
    uuid.UUID(test_uuid)  # Will raise ValueError if invalid
