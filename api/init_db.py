from api.app.database import Base, engine
from api.app import models  # noqa: F401  (import to register models)
print("Creating tables...")
Base.metadata.create_all(bind=engine)
print("Done.")
