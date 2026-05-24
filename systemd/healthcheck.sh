#!/bin/bash
# =============================================================================
#  healthcheck.sh — Smart Room Service Health Monitor
# =============================================================================
#
#  Checks that all critical services are alive and responsive.
#  If any service is down, logs the failure and optionally restarts
#  the entire system via systemd.
#
#  Usage:
#    ./systemd/healthcheck.sh           # Check and report only
#    ./systemd/healthcheck.sh --fix     # Check and restart if anything is down
#
#  This script is called by the smartroom-watchdog.timer every 2 minutes.
# =============================================================================

# Don't use set -e — check commands are expected to fail for down services

# ── Config ──
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." > /dev/null 2>&1 && pwd )"
LOG_TAG="smartroom-health"
FIX_MODE=false

if [[ "${1:-}" == "--fix" ]]; then
    FIX_MODE=true
fi

# Counters
PASS=0
FAIL=0
CHECKS=()

# ── Helper ──
check() {
    local name="$1"
    local cmd="$2"
    
    if eval "$cmd" > /dev/null 2>&1; then
        CHECKS+=("  ✅ $name")
        PASS=$((PASS + 1))
    else
        CHECKS+=("  ❌ $name")
        FAIL=$((FAIL + 1))
    fi
}

# =============================================================================
#  Service Checks
# =============================================================================

# 1. Mosquitto MQTT broker — is port 1883 accepting connections?
check "Mosquitto (MQTT broker)" \
    "timeout 3 bash -c 'echo > /dev/tcp/localhost/1883' 2>/dev/null || mosquitto_pub -h localhost -t 'healthcheck' -m 'ping' -q 0 2>/dev/null"

# 2. Django API — does /api/v1/ respond?
check "Django API (port 8000)" \
    "curl -sf --max-time 5 http://localhost:8000/api/v1/ > /dev/null 2>&1 || curl -sf --max-time 5 http://localhost:8000/ > /dev/null 2>&1"

# 3. ML FastAPI service — does /health or / respond?
check "ML Service (port 5000)" \
    "curl -sf --max-time 5 http://localhost:5000/ > /dev/null 2>&1 || curl -sf --max-time 5 http://localhost:5000/health > /dev/null 2>&1"

# 4. MQTT Logger process — is it running?
check "MQTT Logger (mqtt_logger.py)" \
    "pgrep -f 'mqtt_logger.py' > /dev/null 2>&1"

# 5. Rule Engine process — is it running?
check "Rule Engine (rule_engine.py)" \
    "pgrep -f 'rule_engine.py' > /dev/null 2>&1"

# 6. Data source (simulator or hw_bridge depending on DATA_SOURCE)
source "$BASE_DIR/.env" 2>/dev/null || true
DATA_SOURCE="${DATA_SOURCE:-simulator}"

if [[ "$DATA_SOURCE" == "hardware" ]]; then
    check "Hardware Bridge (hw_bridge.py)" \
        "pgrep -f 'hw_bridge.py' > /dev/null 2>&1"
elif [[ "$DATA_SOURCE" == "simulator" ]]; then
    check "Data Simulator (data_simulator.py)" \
        "pgrep -f 'data_simulator.py' > /dev/null 2>&1"
elif [[ "$DATA_SOURCE" == "both" ]]; then
    check "Hardware Bridge (hw_bridge.py)" \
        "pgrep -f 'hw_bridge.py' > /dev/null 2>&1"
    check "Data Simulator (data_simulator.py)" \
        "pgrep -f 'data_simulator.py' > /dev/null 2>&1"
fi

# =============================================================================
#  Report
# =============================================================================
TOTAL=$((PASS + FAIL))
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Smart Room Health Check — $TIMESTAMP"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DATA_SOURCE=$DATA_SOURCE"
echo ""

for line in "${CHECKS[@]}"; do
    echo "$line"
done

echo ""
echo "  Result: $PASS/$TOTAL passed"

if [[ $FAIL -gt 0 ]]; then
    echo "  ⚠ $FAIL service(s) DOWN"
    logger -t "$LOG_TAG" "UNHEALTHY: $FAIL/$TOTAL services down"

    if [[ "$FIX_MODE" == true ]]; then
        echo ""
        echo "  --fix enabled: Restarting smartroom.service ..."
        logger -t "$LOG_TAG" "AUTO-RESTART triggered"
        sudo systemctl restart smartroom.service
        echo "  Restart command issued. Check: systemctl status smartroom"
    else
        echo ""
        echo "  To auto-fix, run:  ./systemd/healthcheck.sh --fix"
        echo "  Or manually:       sudo systemctl restart smartroom"
    fi
else
    echo "  ✅ All services healthy"
    logger -t "$LOG_TAG" "HEALTHY: $TOTAL/$TOTAL services running"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
