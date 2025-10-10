#!/bin/bash
# Security Scanning Script for DUCK-E
# Security Review: 2025-10-10
# Run this script regularly to detect vulnerabilities

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Create reports directory
REPORT_DIR="/workspaces/duck-e/ducke/security-reports"
mkdir -p "$REPORT_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "🔐 DUCK-E Security Scanning Suite"
echo "=================================="
echo "Report directory: $REPORT_DIR"
echo "Timestamp: $TIMESTAMP"
echo ""

# Function to print status
print_status() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

# 1. Python Dependency Scanning
echo "📦 Step 1/7: Scanning Python dependencies..."
if command -v safety &> /dev/null; then
    safety check --json --output "$REPORT_DIR/safety-report-$TIMESTAMP.json" || print_warning "Safety found vulnerabilities"
    print_status "Safety scan complete"
else
    print_warning "Safety not installed. Run: pip install safety"
fi

if command -v pip-audit &> /dev/null; then
    pip-audit --format json --output "$REPORT_DIR/pip-audit-report-$TIMESTAMP.json" || print_warning "pip-audit found vulnerabilities"
    print_status "pip-audit scan complete"
else
    print_warning "pip-audit not installed. Run: pip install pip-audit"
fi

# 2. Docker Image Scanning
echo ""
echo "🐳 Step 2/7: Scanning Docker images..."
if command -v trivy &> /dev/null; then
    # Build the image first
    docker build -t ducke:latest -f /workspaces/duck-e/ducke/dockerfile /workspaces/duck-e/ducke/

    # Scan with Trivy
    trivy image --severity HIGH,CRITICAL \
        --format json \
        --output "$REPORT_DIR/trivy-report-$TIMESTAMP.json" \
        ducke:latest || print_warning "Trivy found vulnerabilities"

    # Generate human-readable report
    trivy image --severity HIGH,CRITICAL \
        --format table \
        ducke:latest > "$REPORT_DIR/trivy-report-$TIMESTAMP.txt" || true

    print_status "Trivy scan complete"
else
    print_warning "Trivy not installed. Install from: https://github.com/aquasecurity/trivy"
fi

# 3. Software Bill of Materials (SBOM)
echo ""
echo "📋 Step 3/7: Generating SBOM..."
if command -v syft &> /dev/null; then
    syft ducke:latest -o spdx-json > "$REPORT_DIR/sbom-$TIMESTAMP.json"
    syft ducke:latest -o table > "$REPORT_DIR/sbom-$TIMESTAMP.txt"
    print_status "SBOM generated"
else
    print_warning "Syft not installed. Install from: https://github.com/anchore/syft"
fi

# 4. License Compliance
echo ""
echo "⚖️  Step 4/7: Checking license compliance..."
if command -v pip-licenses &> /dev/null; then
    pip-licenses --format=json --output-file "$REPORT_DIR/licenses-$TIMESTAMP.json"
    pip-licenses --format=markdown --output-file "$REPORT_DIR/licenses-$TIMESTAMP.md"
    print_status "License scan complete"
else
    print_warning "pip-licenses not installed. Run: pip install pip-licenses"
fi

# 5. Static Code Analysis
echo ""
echo "🔍 Step 5/7: Running static code analysis..."
if command -v bandit &> /dev/null; then
    bandit -r /workspaces/duck-e/ducke/app \
        -f json \
        -o "$REPORT_DIR/bandit-report-$TIMESTAMP.json" || print_warning "Bandit found issues"

    bandit -r /workspaces/duck-e/ducke/app \
        -f txt \
        -o "$REPORT_DIR/bandit-report-$TIMESTAMP.txt" || true

    print_status "Bandit scan complete"
else
    print_warning "Bandit not installed. Run: pip install bandit"
fi

# 6. Secret Scanning
echo ""
echo "🔑 Step 6/7: Scanning for exposed secrets..."
if command -v gitleaks &> /dev/null; then
    cd /workspaces/duck-e/ducke
    gitleaks detect --report-path "$REPORT_DIR/gitleaks-report-$TIMESTAMP.json" --verbose || print_warning "Gitleaks found potential secrets"
    print_status "Secret scan complete"
else
    print_warning "Gitleaks not installed. Install from: https://github.com/gitleaks/gitleaks"
fi

# 7. Container Configuration Audit
echo ""
echo "⚙️  Step 7/7: Auditing container configuration..."
if command -v docker-bench-security &> /dev/null; then
    docker run --rm --net host --pid host --userns host --cap-add audit_control \
        -e DOCKER_CONTENT_TRUST=$DOCKER_CONTENT_TRUST \
        -v /var/lib:/var/lib \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v /usr/lib/systemd:/usr/lib/systemd \
        -v /etc:/etc --label docker_bench_security \
        docker/docker-bench-security > "$REPORT_DIR/docker-bench-$TIMESTAMP.txt" || true

    print_status "Docker bench audit complete"
else
    print_warning "Docker bench not available"
fi

# Generate summary report
echo ""
echo "📊 Generating summary report..."

SUMMARY_FILE="$REPORT_DIR/security-summary-$TIMESTAMP.md"

cat > "$SUMMARY_FILE" << EOF
# Security Scan Summary
**Date:** $(date)
**Project:** DUCK-E Voice Assistant

## Scan Results

### 1. Dependency Vulnerabilities
- **Safety Report:** safety-report-$TIMESTAMP.json
- **pip-audit Report:** pip-audit-report-$TIMESTAMP.json

### 2. Container Security
- **Trivy Report:** trivy-report-$TIMESTAMP.json
- **Docker Bench:** docker-bench-$TIMESTAMP.txt

### 3. Code Analysis
- **Bandit Report:** bandit-report-$TIMESTAMP.json

### 4. Supply Chain Security
- **SBOM:** sbom-$TIMESTAMP.json
- **Licenses:** licenses-$TIMESTAMP.json

### 5. Secret Detection
- **Gitleaks Report:** gitleaks-report-$TIMESTAMP.json

## Critical Findings

EOF

# Extract critical findings from Trivy
if [ -f "$REPORT_DIR/trivy-report-$TIMESTAMP.json" ]; then
    CRITICAL_COUNT=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity == "CRITICAL")] | length' "$REPORT_DIR/trivy-report-$TIMESTAMP.json" 2>/dev/null || echo "0")
    HIGH_COUNT=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity == "HIGH")] | length' "$REPORT_DIR/trivy-report-$TIMESTAMP.json" 2>/dev/null || echo "0")

    echo "### Container Vulnerabilities" >> "$SUMMARY_FILE"
    echo "- CRITICAL: $CRITICAL_COUNT" >> "$SUMMARY_FILE"
    echo "- HIGH: $HIGH_COUNT" >> "$SUMMARY_FILE"
    echo "" >> "$SUMMARY_FILE"
fi

# Check for secrets
if [ -f "$REPORT_DIR/gitleaks-report-$TIMESTAMP.json" ]; then
    SECRET_COUNT=$(jq 'length' "$REPORT_DIR/gitleaks-report-$TIMESTAMP.json" 2>/dev/null || echo "0")

    echo "### Exposed Secrets" >> "$SUMMARY_FILE"
    echo "- Found: $SECRET_COUNT potential secret(s)" >> "$SUMMARY_FILE"
    echo "" >> "$SUMMARY_FILE"

    if [ "$SECRET_COUNT" -gt 0 ]; then
        print_error "⚠️  WARNING: $SECRET_COUNT potential secrets found!"
    fi
fi

echo "## Recommendations" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"
echo "1. Review all CRITICAL and HIGH severity vulnerabilities" >> "$SUMMARY_FILE"
echo "2. Update dependencies with known CVEs" >> "$SUMMARY_FILE"
echo "3. Remove any exposed secrets immediately" >> "$SUMMARY_FILE"
echo "4. Address container configuration issues" >> "$SUMMARY_FILE"
echo "5. Run this scan weekly or before each deployment" >> "$SUMMARY_FILE"

print_status "Summary report generated: $SUMMARY_FILE"

echo ""
echo "=================================="
echo "✅ Security scan complete!"
echo ""
echo "Reports saved to: $REPORT_DIR"
echo ""
echo "Next steps:"
echo "1. Review $SUMMARY_FILE"
echo "2. Address CRITICAL and HIGH severity issues"
echo "3. Update dependencies regularly"
echo "4. Schedule weekly automated scans"
echo ""

# Return non-zero exit code if critical issues found
if [ -f "$REPORT_DIR/trivy-report-$TIMESTAMP.json" ]; then
    CRITICAL_COUNT=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity == "CRITICAL")] | length' "$REPORT_DIR/trivy-report-$TIMESTAMP.json" 2>/dev/null || echo "0")

    if [ "$CRITICAL_COUNT" -gt 0 ]; then
        print_error "⚠️  CRITICAL vulnerabilities found! Please address before production deployment."
        exit 1
    fi
fi

exit 0
