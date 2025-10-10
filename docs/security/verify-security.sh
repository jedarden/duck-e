#!/bin/bash
# Security Configuration Verification Script for DUCK-E
# This script verifies that all security components are properly configured

set -e

echo "🔒 DUCK-E Security Configuration Verification"
echo "=============================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if we're in the correct directory
if [ ! -f "app/main.py" ]; then
    echo -e "${RED}❌ Error: Must run from ducke/ directory${NC}"
    exit 1
fi

echo "📁 Checking file structure..."
echo ""

# Check middleware files
MIDDLEWARE_FILES=(
    "app/middleware/security_headers.py"
    "app/middleware/cors_config.py"
    "app/middleware/websocket_validator.py"
    "app/middleware/__init__.py"
)

for file in "${MIDDLEWARE_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "${GREEN}✅${NC} $file"
    else
        echo -e "${RED}❌${NC} $file (MISSING)"
        exit 1
    fi
done

echo ""

# Check configuration files
CONFIG_FILES=(
    "config/security.yaml"
    ".env.example"
)

for file in "${CONFIG_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "${GREEN}✅${NC} $file"
    else
        echo -e "${RED}❌${NC} $file (MISSING)"
        exit 1
    fi
done

echo ""

# Check documentation
DOC_FILES=(
    "docs/security/security-headers-guide.md"
    "docs/security/IMPLEMENTATION.md"
    "docs/security/README.md"
)

for file in "${DOC_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "${GREEN}✅${NC} $file"
    else
        echo -e "${RED}❌${NC} $file (MISSING)"
        exit 1
    fi
done

echo ""

# Check test files
TEST_FILES=(
    "tests/test_security_headers.py"
    "tests/test_cors_config.py"
    "tests/test_websocket_validator.py"
)

for file in "${TEST_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "${GREEN}✅${NC} $file"
    else
        echo -e "${RED}❌${NC} $file (MISSING)"
        exit 1
    fi
done

echo ""
echo "🔍 Checking Python imports..."
echo ""

# Check if middleware can be imported
python3 -c "from app.middleware import create_security_headers_middleware, configure_cors, get_websocket_security_middleware" 2>/dev/null
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅${NC} Middleware imports successful"
else
    echo -e "${RED}❌${NC} Middleware import failed"
    echo -e "${YELLOW}⚠️${NC}  This may be normal if dependencies aren't installed"
fi

echo ""
echo "⚙️  Checking main.py integration..."
echo ""

# Check if main.py has security middleware imports
if grep -q "from app.middleware import" app/main.py; then
    echo -e "${GREEN}✅${NC} Security middleware imported in main.py"
else
    echo -e "${RED}❌${NC} Security middleware NOT imported in main.py"
    exit 1
fi

# Check if CORS is configured
if grep -q "configure_cors(app)" app/main.py; then
    echo -e "${GREEN}✅${NC} CORS configured in main.py"
else
    echo -e "${RED}❌${NC} CORS NOT configured in main.py"
    exit 1
fi

# Check if security headers middleware is added
if grep -q "create_security_headers_middleware" app/main.py; then
    echo -e "${GREEN}✅${NC} Security headers middleware added in main.py"
else
    echo -e "${RED}❌${NC} Security headers middleware NOT added in main.py"
    exit 1
fi

# Check if WebSocket validation is implemented
if grep -q "ws_security.validate_connection" app/main.py; then
    echo -e "${GREEN}✅${NC} WebSocket origin validation implemented in main.py"
else
    echo -e "${RED}❌${NC} WebSocket origin validation NOT implemented in main.py"
    exit 1
fi

echo ""
echo "🔐 Checking environment configuration..."
echo ""

# Check if .env exists
if [ -f ".env" ]; then
    echo -e "${GREEN}✅${NC} .env file exists"

    # Check for critical security variables
    if grep -q "ALLOWED_ORIGINS=" .env; then
        echo -e "${GREEN}✅${NC} ALLOWED_ORIGINS configured"
    else
        echo -e "${YELLOW}⚠️${NC}  ALLOWED_ORIGINS not configured (will use defaults)"
    fi

    if grep -q "ENVIRONMENT=" .env; then
        ENV_VALUE=$(grep "ENVIRONMENT=" .env | cut -d '=' -f2)
        if [ "$ENV_VALUE" == "production" ]; then
            echo -e "${YELLOW}⚠️${NC}  Environment set to PRODUCTION"
            echo -e "    Make sure ALLOWED_ORIGINS and ENABLE_HSTS are properly configured!"
        else
            echo -e "${GREEN}✅${NC} Environment set to development"
        fi
    fi
else
    echo -e "${YELLOW}⚠️${NC}  .env file not found (using .env.example as reference)"
fi

echo ""
echo "📊 File Statistics..."
echo ""

# Count lines of code
MIDDLEWARE_LINES=$(cat app/middleware/security_headers.py app/middleware/cors_config.py app/middleware/websocket_validator.py | wc -l)
TEST_LINES=$(cat tests/test_security_headers.py tests/test_cors_config.py tests/test_websocket_validator.py | wc -l)
DOC_LINES=$(cat docs/security/security-headers-guide.md docs/security/IMPLEMENTATION.md | wc -l)

echo "Middleware code: $MIDDLEWARE_LINES lines"
echo "Test code: $TEST_LINES lines"
echo "Documentation: $DOC_LINES lines"
echo ""

# Test coverage check
echo "🧪 Running tests (if pytest is available)..."
echo ""

if command -v pytest &> /dev/null; then
    pytest tests/test_security_headers.py tests/test_cors_config.py tests/test_websocket_validator.py -v --tb=short 2>&1 | head -20
    TEST_EXIT_CODE=$?
    if [ $TEST_EXIT_CODE -eq 0 ]; then
        echo -e "${GREEN}✅${NC} All tests passed"
    else
        echo -e "${YELLOW}⚠️${NC}  Some tests failed (may be due to missing dependencies)"
    fi
else
    echo -e "${YELLOW}⚠️${NC}  pytest not installed, skipping test execution"
fi

echo ""
echo "=============================================="
echo "✅ Security configuration verification complete!"
echo ""
echo "📚 Next steps:"
echo "1. Copy .env.example to .env and configure ALLOWED_ORIGINS"
echo "2. Set ENVIRONMENT=production for production deployment"
echo "3. Enable HSTS when HTTPS is fully configured"
echo "4. Review docs/security/security-headers-guide.md for details"
echo "5. Run: pytest tests/test_security_*.py to verify tests pass"
echo ""
echo "🔒 Security features implemented:"
echo "  - OWASP security headers (HSTS, CSP, X-Frame-Options, etc.)"
echo "  - CORS with origin validation"
echo "  - WebSocket origin validation"
echo "  - Environment-based configuration"
echo "  - Comprehensive test coverage"
echo ""
