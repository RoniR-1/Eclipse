from financial_twin.database import initialize_from_environment
from financial_twin.server import load_env


load_env()
print("Database seeded" if initialize_from_environment() else "Database already contains data; seed skipped")
