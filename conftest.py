import os

# Test defaults; a value already in the environment (CI's DATABASE_URL) wins.
os.environ.setdefault(
    "DATABASE_URL", "postgresql://purchase:purchase@localhost:5435/purchase"
)
os.environ.setdefault("PURCHASE_TOKEN", "test-token")
os.environ.setdefault("PURCHASE_TEST_RESET", "1")
os.environ.setdefault("SEED_STARTER_SPACE", "true")
