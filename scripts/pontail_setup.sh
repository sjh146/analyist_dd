#!/bin/bash
# Pontail Plugin Setup Script
set -e

echo "=== Pontail Plugin Setup ==="

# Install pontail package (try pip first, then npm)
if command -v pip3 &>/dev/null; then
    pip3 install pontail 2>/dev/null && echo "pontail installed via pip" || echo "pip install failed, trying npm..."
fi

if ! python3 -c "import pontail" 2>/dev/null; then
    if command -v npm &>/dev/null; then
        npm install -g pontail 2>/dev/null && echo "pontail installed via npm" || echo "WARNING: pontail install failed"
    fi
fi

# Verify installation
if python3 -c "import pontail; print(f'pontail version: {pontail.__version__}')" 2>/dev/null; then
    echo "✅ Pontail installed successfully"
else
    echo "⚠️  Pontail not found — create stub plugin"
    mkdir -p /home/dduckbeagy/analyist_dd/.omo/plugins/pontail
    cat > /home/dduckbeagy/analyist_dd/.omo/plugins/pontail/__init__.py << 'EOF'
"""Pontail plugin stub - provides plan/evidence/status endpoints."""
__version__ = "0.1.0"

def get_routes():
    return {
        "/plans": {"description": "Plan management"},
        "/evidence": {"description": "Evidence tracking"},
        "/status": {"description": "Plugin status"},
    }
EOF
    echo "✅ Pontail stub created at .omo/plugins/pontail/"
fi

echo "=== Pontail Setup Complete ==="
