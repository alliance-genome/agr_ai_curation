#!/usr/bin/env python3
"""
Test script to verify the structure of our migrated code
without requiring Unstructured to be installed
"""

import ast
import sys
from pathlib import Path


def check_file_structure(filepath):
    """Check a Python file's structure and imports"""
    print(f"\n{'='*60}")
    print(f"Checking: {filepath}")
    print("=" * 60)

    with open(filepath, "r") as f:
        content = f.read()

    # Parse the AST
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        print(f"❌ Syntax Error: {e}")
        return False

    # Check for PyMuPDF references
    pymupdf_found = False
    if "pymupdf" in content.lower() or "fitz" in content:
        # Exclude comments
        for line in content.split("\n"):
            if not line.strip().startswith("#"):
                if "pymupdf" in line.lower() or "fitz" in line:
                    print(f"❌ Found PyMuPDF reference: {line.strip()}")
                    pymupdf_found = True

    if not pymupdf_found:
        print("✅ No PyMuPDF references found")

    # Extract imports
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")

    # Check for Unstructured imports
    unstructured_imports = [imp for imp in imports if "unstructured" in imp]
    if unstructured_imports:
        print("\n📦 Unstructured imports found:")
        for imp in unstructured_imports:
            print(f"  - {imp}")

    # Extract classes
    classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)

    if classes:
        print("\n📋 Classes defined:")
        for cls in classes:
            print(f"  - {cls}")

    # Extract main functions
    functions = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            functions.append(node.name)

    if functions:
        print("\n🔧 Top-level functions:")
        for func in functions[:5]:  # Show first 5
            print(f"  - {func}")

    return not pymupdf_found


def check_test_file(filepath):
    """Check test file structure"""
    print(f"\n{'='*60}")
    print(f"Checking Test: {filepath}")
    print("=" * 60)

    with open(filepath, "r") as f:
        content = f.read()

    # Check imports
    if "from lib.pdf_processor import" in content:
        print("✅ Imports from lib.pdf_processor")
    elif "from lib.pdf_processor_unstructured import" in content:
        print("❌ Still imports from pdf_processor_unstructured")
        return False

    if "from lib.chunk_manager import" in content:
        print("✅ Imports from lib.chunk_manager")
    elif "from lib.chunk_manager_unstructured import" in content:
        print("❌ Still imports from chunk_manager_unstructured")
        return False

    # Check for PyMuPDF references
    if "pymupdf" in content.lower() or "fitz" in content:
        print("❌ Found PyMuPDF reference in test")
        return False
    else:
        print("✅ No PyMuPDF references")

    # Count test methods
    test_count = content.count("def test_")
    print(f"📊 Found {test_count} test methods")

    return True


def main():
    """Main test runner"""
    print("\n" + "=" * 60)
    print("STRUCTURE VERIFICATION FOR UNSTRUCTURED MIGRATION")
    print("=" * 60)

    all_good = True

    # Check main library files
    lib_files = ["lib/pdf_processor.py", "lib/chunk_manager.py"]

    for filepath in lib_files:
        if not check_file_structure(filepath):
            all_good = False

    # Check test files
    test_files = [
        "tests/unit/test_pdf_processor.py",
        "tests/unit/test_chunk_manager.py",
        "tests/integration/test_real_pdf.py",
    ]

    for filepath in test_files:
        if Path(filepath).exists():
            if not check_test_file(filepath):
                all_good = False
        else:
            print(f"\n⚠️ Test file not found: {filepath}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    if all_good:
        print("✅ All structure checks passed!")
        print("\nNext steps:")
        print("1. Docker container needs to be rebuilt with Unstructured dependencies")
        print("2. This will install:")
        print("   - unstructured[pdf,local-inference]==0.16.11")
        print("   - System packages: tesseract-ocr, poppler-utils, libmagic1")
        print("   - ML dependencies: torch, transformers, onnxruntime")
        print("\n⚠️ Note: The build will take 5-10 minutes due to large ML dependencies")
        return 0
    else:
        print("❌ Some issues found - please review above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
