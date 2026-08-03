#!/bin/bash
# Verify all optional extras install correctly
set -e

EXTRAS=("redis" "cache" "tracing" "observability" "drf" "fastapi" "tasks" "async")

echo "=========================================="
echo "Testing django-nova extras installation"
echo "=========================================="
echo ""

for extra in "${EXTRAS[@]}"; do
    echo "→ Testing extra: $extra"
    
    # Create isolated virtual environment
    rm -rf ".venv-test-$extra"
    python3.12 -m venv ".venv-test-$extra"
    source ".venv-test-$extra/bin/activate"
    
    # Install with extra
    pip install -e ".[$extra]" --quiet
    
    # Verify nova imports
    python -c "import nova; print('  ✓ nova imported successfully')"
    
    # Verify extra-specific modules
    case $extra in
        "redis"|"cache")
            python -c "from nova.cache.backends.redis import RedisCacheBackend; print('  ✓ Redis backend available')" 2>/dev/null \
                || python -c "import redis; print('  ✓ Redis client available')"
            ;;
        "tracing")
            python -c "from nova.core.tracing import nova_span; print('  ✓ tracing module available')"
            ;;
        "observability")
            python -c "from nova.core.observability import setup_nova_logging; print('  ✓ observability module available')"
            ;;
        "drf")
            python -c "from nova.ecosystem.drf import to_drf_serializer; print('  ✓ DRF integration available')"
            ;;
        "fastapi")
            python -c "from nova.ecosystem.fastapi import NovaRouter; print('  ✓ FastAPI integration available')" 2>/dev/null \
                || python -c "import fastapi; print('  ✓ FastAPI available')"
            ;;
        "tasks")
            python -c "from nova.tasks import Task; print('  ✓ task queue available')" 2>/dev/null \
                || python -c "import asyncio_throttle; print('  ✓ asyncio-throttle available')"
            ;;
        "async")
            python -c "import asyncpg; print('  ✓ asyncpg available')"
            ;;
    esac

    deactivate
    rm -rf ".venv-test-$extra"
    echo ""
done

echo "=========================================="
echo "✅ All extras validated successfully!"
echo "=========================================="