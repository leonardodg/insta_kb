from fastapi import FastAPI

from core.settings.config import settings

app = FastAPI(
    root_path=settings.PROJECT_ROOT,
    title=settings.APP_NAME,
    description="Insta Knowledge Base - FastAPI and FastMCP",
    version="0.1.0",
)


@app.get("/healthcheck", tags=["health"])
async def healthcheck():
    return {"app_name": settings.APP_NAME, "status": "healthy"}


# if __name__ == "__main__":
#     uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT)
