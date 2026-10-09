import os

from fastapi import FastAPI

app = FastAPI(title="Engine", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    # The deploy job polls this until `commit` matches the SHA it deployed.
    return {"status": "ok", "commit": os.environ.get("RENDER_GIT_COMMIT", "local")}
