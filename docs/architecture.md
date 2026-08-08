
### `docs/architecture.md`

```markdown
# Django Nova Architecture

## The general scheme

```text
┌─────────────────────────────────────────────────────────────┐
│                        Request Layer                        │
│                      (Django Views/DRF)                     │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                   Validation Layer (Pydantic)               │
│         A single source of truth for all operations         │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                    Business Logic Layer                     │
│              (NovaModel, NovaQuerySet, Services)            │
└───────────────────────┬─────────────────────────────────────┘
                        │
            ┌───────────┴───────────┐
            │                       │
            ▼                       ▼
┌──────────────────┐      ┌──────────────────────┐
│   Cache Layer    │      │   Database Layer     │
│     (Redis)      │      │   (PostgreSQL)       │
│  Auto-invalid.   │      │  Concurrent Index    │
└──────────────────┘      └──────────────────────┘