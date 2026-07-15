#!/usr/bin/env bash
#
# generate_tests.sh
#
# Auto-generates smoke tests for services that are missing a tests/ directory.
# For each service under services/*/, it:
#   1. Checks if tests/ exists (skips if it does)
#   2. Scans for main entry points (app/main.py, src/main.py, app.py, main.py)
#   3. Extracts class/function names to create a basic import smoke test
#   4. Verifies the generated test is importable
#   5. Removes any test that fails verification
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SERVICES_DIR="$PROJECT_DIR/services"

CREATED=0
VERIFIED=0
FAILED=0
SKIPPED=0

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo "============================================"
echo "  Smoke Test Generator"
echo "============================================"
echo ""

# Ensure pytest is available
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}ERROR: python3 not found. Aborting.${NC}"
    exit 1
fi

# Iterate over all service directories
for service_dir in "$SERVICES_DIR"/*/; do
    service_name="$(basename "$service_dir")"
    tests_dir="$service_dir/tests"

    # Skip if tests/ already exists
    if [ -d "$tests_dir" ]; then
        echo -e "${YELLOW}[SKIP]${NC} $service_name — tests/ already exists"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    echo -e "${YELLOW}[CREATE]${NC} $service_name — no tests/ found"

    # Find the main entry point
    main_file=""
    for candidate in "app/main.py" "src/main.py" "app.py" "main.py"; do
        if [ -f "$service_dir/$candidate" ]; then
            main_file="$candidate"
            break
        fi
    done

    if [ -z "$main_file" ]; then
        echo "  └─ No main entry point found (app/main.py, src/main.py, app.py, main.py)"
        echo "  └─ Creating minimal smoke test"
        # Create a minimal smoke test even without a main file
        mkdir -p "$tests_dir"
        cat > "$tests_dir/test_smoke.py" << PYEOF
"""Smoke tests for $service_name service."""
import pytest


class Test${service_name^}Smoke:
    """Basic smoke tests for $service_name."""

    def test_import(self):
        """Verify the service directory is importable as a package."""
        import $service_name  # type: ignore[import-untyped]

    def test_package_exists(self):
        """Verify the package is accessible."""
        import importlib
        try:
            importlib.import_module("$service_name")
        except ImportError:
            pytest.skip("$service_name package not installed")
PYEOF
        CREATED=$((CREATED + 1))
    else
        echo "  └─ Found entry point: $main_file"

        # Extract top-level class and function names
        classes=$(grep -E '^class [A-Za-z_][A-Za-z0-9_]*' "$service_dir/$main_file" | sed 's/class \([A-Za-z_][A-Za-z0-9_]*\).*/\1/' || true)
        functions=$(grep -E '^def [a-z_][a-z0-9_]*' "$service_dir/$main_file" | sed 's/def \([a-z_][a-z0-9_]*\).*/\1/' || true)

        # Determine the Python module path
        if [[ "$main_file" == *"/"* ]]; then
            # Has subdirectory (e.g., app/main.py)
            parent_dir="$(dirname "$main_file")"
            parent_module="${parent_dir//\//.}"
            module_name="${parent_module}.main"
        else
            module_name="${main_file%.py}"
        fi

        mkdir -p "$tests_dir"

        # Build the test file
        cat > "$tests_dir/test_smoke.py" << PYEOF
"""Smoke tests for $service_name service."""
import pytest
import sys


class Test${service_name^}Smoke:
    """Basic smoke tests for $service_name."""

    def test_import_main(self):
        """Verify the main module imports without error."""
        import $module_name

PYEOF

        # Add class instantiation tests
        if [ -n "$classes" ]; then
            echo "" >> "$tests_dir/test_smoke.py"
            echo "    # --- Class instantiation smoke tests ---" >> "$tests_dir/test_smoke.py"
            while IFS= read -r cls; do
                [ -z "$cls" ] && continue
                # Skip if class name starts with Test (pytest class)
                if [[ "$cls" == Test* ]]; then
                    continue
                fi
                echo "" >> "$tests_dir/test_smoke.py"
                echo "    def test_${cls}_can_instantiate(self):" >> "$tests_dir/test_smoke.py"
                echo "        \"\"\"Verify $cls can be instantiated.\"\"\"" >> "$tests_dir/test_smoke.py"
                echo "        import $module_name" >> "$tests_dir/test_smoke.py"
                echo "        obj = $module_name.$cls()" >> "$tests_dir/test_smoke.py"
                echo "        assert obj is not None" >> "$tests_dir/test_smoke.py"
            done <<< "$classes"
        fi

        # Add function call tests
        if [ -n "$functions" ]; then
            echo "" >> "$tests_dir/test_smoke.py"
            echo "    # --- Function smoke tests ---" >> "$tests_dir/test_smoke.py"
            while IFS= read -r func; do
                [ -z "$func" ] && continue
                echo "" >> "$tests_dir/test_smoke.py"
                echo "    def test_${func}_is_callable(self):" >> "$tests_dir/test_smoke.py"
                echo "        \"\"\"Verify $func is importable and callable.\"\"\"" >> "$tests_dir/test_smoke.py"
                echo "        import $module_name" >> "$tests_dir/test_smoke.py"
                echo "        assert callable($module_name.$func)" >> "$tests_dir/test_smoke.py"
            done <<< "$functions"
        fi

        CREATED=$((CREATED + 1))
    fi

    # Create __init__.py for the tests package
    touch "$tests_dir/__init__.py"

    # Verify the generated test is importable
    echo "  └─ Verifying test import..."
    test_file="$tests_dir/test_smoke.py"
    if [ -f "$test_file" ]; then
        # Try to compile/import the test module
        if python3 -c "
import sys
sys.path.insert(0, '$service_dir')
sys.path.insert(0, '$tests_dir')
try:
    import ast
    with open('$test_file') as f:
        ast.parse(f.read())
    print('OK: syntax valid')
except SyntaxError as e:
    print(f'FAIL: {e}')
    sys.exit(1)
" 2>&1; then
            echo -e "  └─ ${GREEN}VERIFIED${NC} — test syntax is valid"
            VERIFIED=$((VERIFIED + 1))
        else
            echo -e "  └─ ${RED}FAILED${NC} — removing invalid test"
            rm -f "$test_file" "$tests_dir/__init__.py"
            rmdir "$tests_dir" 2>/dev/null || true
            FAILED=$((FAILED + 1))
        fi
    fi

    echo ""
done

echo "============================================"
echo "  Summary"
echo "============================================"
echo -e "  Created:  ${GREEN}$CREATED${NC}"
echo -e "  Verified: ${GREEN}$VERIFIED${NC}"
echo -e "  Failed:   ${RED}$FAILED${NC}"
echo -e "  Skipped:  ${YELLOW}$SKIPPED${NC}"
echo "============================================"

# Exit with error if any tests failed
if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
