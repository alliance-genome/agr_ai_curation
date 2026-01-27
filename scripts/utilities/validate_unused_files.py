#!/usr/bin/env python3
"""
Multi-tool validation for finding unused files and dead code.

This script layers multiple analysis techniques to validate findings:
1. Import tracing (static AST analysis)
2. Coverage analysis (runtime execution)
3. Test file collection (pytest)
4. Config file usage (grep-based search)

Run after generating coverage data:
    docker compose exec backend coverage run -m pytest
    docker compose exec backend coverage json -o /tmp/coverage.json
    docker cp $(docker compose ps -q backend):/tmp/coverage.json ./coverage.json
    python3 scripts/utilities/validate_unused_files.py
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Set, Dict, List, Tuple
from collections import defaultdict

# Import our existing unused files finder
sys.path.insert(0, str(Path(__file__).parent))
from find_unused_files import find_all_python_files, trace_imports, file_to_module_name


def load_coverage_data(coverage_file: Path) -> Dict[str, float]:
    """Load coverage.py JSON report and extract file coverage percentages.

    Args:
        coverage_file: Path to coverage.json

    Returns:
        Dict mapping file paths to coverage percentages
    """
    if not coverage_file.exists():
        print(f"⚠️  Coverage file not found: {coverage_file}")
        print("   Run: docker compose exec backend coverage run -m pytest")
        print("        docker compose exec backend coverage json -o /tmp/coverage.json")
        print("        docker cp $(docker compose ps -q backend):/tmp/coverage.json ./coverage.json")
        return {}

    with open(coverage_file) as f:
        data = json.load(f)

    coverage_by_file = {}
    for file_path, file_data in data.get("files", {}).items():
        summary = file_data.get("summary", {})
        percent = summary.get("percent_covered", 0.0)
        coverage_by_file[file_path] = percent

    return coverage_by_file


def get_pytest_collected_files(backend_dir: Path) -> Set[Path]:
    """Get list of test files that pytest discovers.

    Args:
        backend_dir: Path to backend directory

    Returns:
        Set of test file paths that pytest finds
    """
    try:
        # Run pytest --collect-only from backend directory
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "backend", "pytest", "--collect-only", "-q"],
            cwd=backend_dir.parent,
            capture_output=True,
            text=True,
            timeout=30
        )

        collected_files = set()
        for line in result.stdout.splitlines():
            # pytest outputs lines like: "tests/test_foo.py::test_bar"
            if "::" in line:
                file_part = line.split("::")[0].strip()
                if file_part.endswith(".py"):
                    # Convert to absolute path
                    test_file = backend_dir / file_part
                    if test_file.exists():
                        collected_files.add(test_file)

        return collected_files
    except subprocess.TimeoutExpired:
        print("⚠️  pytest --collect-only timed out")
        return set()
    except Exception as e:
        print(f"⚠️  Failed to run pytest --collect-only: {e}")
        return set()


def find_unused_config_files(project_root: Path, extensions: List[str]) -> List[Tuple[Path, bool]]:
    """Find configuration files that aren't referenced in code.

    Args:
        project_root: Root directory of project
        extensions: List of file extensions to check (e.g., ['.json', '.yaml'])

    Returns:
        List of (file_path, is_referenced) tuples
    """
    backend_dir = project_root / "backend"

    # Find all config files
    config_files = []
    for ext in extensions:
        config_files.extend(backend_dir.rglob(f"*{ext}"))

    # Exclude common directories
    excluded_dirs = {".git", "__pycache__", "node_modules", ".pytest_cache", "venv"}
    config_files = [
        f for f in config_files
        if not any(excluded in f.parts for excluded in excluded_dirs)
    ]

    results = []
    for config_file in config_files:
        filename = config_file.name

        # Search for filename in Python files
        try:
            result = subprocess.run(
                ["grep", "-r", filename, str(backend_dir / "src"), "--include=*.py", "-l"],
                capture_output=True,
                text=True,
                timeout=5
            )
            is_referenced = bool(result.stdout.strip())
            results.append((config_file, is_referenced))
        except Exception:
            # If grep fails, assume referenced to be safe
            results.append((config_file, True))

    return results


def main():
    """Run multi-tool validation analysis."""
    # Setup paths
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent
    backend_dir = project_root / "backend"
    src_root = backend_dir / "src"
    entry_file = backend_dir / "main.py"
    coverage_file = project_root / "coverage.json"

    print("="*70)
    print("Multi-Tool Unused File Validation")
    print("="*70)
    print()

    # ========================================
    # TOOL 1: Import Tracing (Static)
    # ========================================
    print("🔍 TOOL 1: Static Import Tracing")
    print("-" * 70)

    all_files = find_all_python_files(src_root)
    imported = trace_imports(entry_file, all_files, src_root)
    source_modules_imported = imported & set(all_files.keys())

    unused_by_imports = set(all_files.keys()) - source_modules_imported

    print(f"   Total Python files:     {len(all_files)}")
    print(f"   Imported files:         {len(source_modules_imported)}")
    print(f"   Unused by imports:      {len(unused_by_imports)}")
    print()

    # ========================================
    # TOOL 2: Coverage Analysis (Runtime)
    # ========================================
    print("📊 TOOL 2: Coverage Analysis (Runtime)")
    print("-" * 70)

    coverage_data = load_coverage_data(coverage_file)

    if coverage_data:
        # Map coverage file paths to module names
        zero_coverage_files = set()
        low_coverage_files = []  # (module, coverage_pct)

        for file_path, coverage_pct in coverage_data.items():
            # Convert absolute coverage path to module name
            try:
                abs_path = Path(file_path)
                if abs_path.is_relative_to(src_root):
                    module_name = file_to_module_name(abs_path, src_root)

                    if coverage_pct == 0.0:
                        zero_coverage_files.add(module_name)
                    elif coverage_pct < 20.0:
                        low_coverage_files.append((module_name, coverage_pct))
            except (ValueError, KeyError):
                pass

        print(f"   Files with 0% coverage:       {len(zero_coverage_files)}")
        print(f"   Files with <20% coverage:     {len(low_coverage_files)}")

        # Find intersection: unused by imports AND zero coverage
        confirmed_unused = unused_by_imports & zero_coverage_files
        print(f"   ✅ CONFIRMED UNUSED:          {len(confirmed_unused)}")
        print(f"      (0% coverage + not imported)")
        print()
    else:
        print("   ⚠️  No coverage data available - skipping runtime validation")
        print()
        confirmed_unused = set()
        low_coverage_files = []

    # ========================================
    # TOOL 3: Test Collection
    # ========================================
    print("🧪 TOOL 3: Test File Collection")
    print("-" * 70)

    # Find all test files
    test_files = set(backend_dir.rglob("test_*.py"))
    test_files |= set(backend_dir.rglob("*_test.py"))

    # Get pytest-collected files
    collected_test_files = get_pytest_collected_files(backend_dir)

    orphaned_tests = test_files - collected_test_files

    print(f"   Total test files:             {len(test_files)}")
    print(f"   Collected by pytest:          {len(collected_test_files)}")
    print(f"   Orphaned tests:               {len(orphaned_tests)}")
    print()

    # ========================================
    # TOOL 4: Config File Usage
    # ========================================
    print("⚙️  TOOL 4: Configuration File Usage")
    print("-" * 70)

    config_results = find_unused_config_files(project_root, [".json", ".yaml", ".yml"])
    unused_configs = [f for f, ref in config_results if not ref]

    print(f"   Total config files checked:   {len(config_results)}")
    print(f"   Referenced in code:           {len(config_results) - len(unused_configs)}")
    print(f"   Potentially unused:           {len(unused_configs)}")
    print()

    # ========================================
    # SUMMARY & RECOMMENDATIONS
    # ========================================
    print()
    print("="*70)
    print("📋 SUMMARY & RECOMMENDATIONS")
    print("="*70)
    print()

    # High confidence unused files
    if confirmed_unused:
        print("🔴 HIGH CONFIDENCE - Safe to Remove:")
        print("   (Not imported AND 0% coverage)")
        print()
        for module in sorted(confirmed_unused)[:10]:
            file_path = all_files[module].relative_to(backend_dir)
            print(f"   - {file_path}")
        if len(confirmed_unused) > 10:
            print(f"   ... and {len(confirmed_unused) - 10} more")
        print()

    # Medium confidence
    medium_confidence = unused_by_imports - confirmed_unused
    if medium_confidence and coverage_data:
        print("🟡 MEDIUM CONFIDENCE - Review Before Removing:")
        print("   (Not imported but HAS coverage - may be dynamically loaded)")
        print()
        for module in sorted(medium_confidence)[:5]:
            file_path = all_files[module].relative_to(backend_dir)
            cov = coverage_data.get(str(all_files[module]), "N/A")
            print(f"   - {file_path} (coverage: {cov}%)")
        if len(medium_confidence) > 5:
            print(f"   ... and {len(medium_confidence) - 5} more")
        print()

    # Low coverage but imported
    if low_coverage_files:
        print("🟠 IMPORTED BUT MOSTLY DEAD CODE:")
        print("   (Files are imported but <20% of code runs)")
        print()
        for module, cov in sorted(low_coverage_files, key=lambda x: x[1])[:5]:
            if module in all_files:
                file_path = all_files[module].relative_to(backend_dir)
                print(f"   - {file_path} ({cov:.1f}% coverage)")
        if len(low_coverage_files) > 5:
            print(f"   ... and {len(low_coverage_files) - 5} more")
        print()

    # Orphaned tests
    if orphaned_tests:
        print("🧪 ORPHANED TEST FILES:")
        print("   (Test files not discovered by pytest)")
        print()
        for test_file in sorted(orphaned_tests)[:5]:
            print(f"   - {test_file.relative_to(backend_dir)}")
        if len(orphaned_tests) > 5:
            print(f"   ... and {len(orphaned_tests) - 5} more")
        print()

    # Unused configs
    if unused_configs:
        print("⚙️  UNUSED CONFIGURATION FILES:")
        print("   (Not referenced in any Python code)")
        print()
        for config_file in sorted(unused_configs)[:5]:
            print(f"   - {config_file.relative_to(project_root)}")
        if len(unused_configs) > 5:
            print(f"   ... and {len(unused_configs) - 5} more")
        print()

    print("="*70)
    print("Next Steps:")
    print("1. Review HIGH CONFIDENCE files first - safest to remove")
    print("2. Manually verify MEDIUM CONFIDENCE - may be dynamic imports")
    print("3. Refactor files with low coverage to remove dead code")
    print("4. Fix or remove orphaned test files")
    print("5. Clean up unused config files")
    print("="*70)


if __name__ == "__main__":
    main()
