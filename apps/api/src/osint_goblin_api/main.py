from fastapi import FastAPI

def create_app() -> FastAPI:
    app = FastAPI(title="OSINT Goblin API", version="2026.05.0")
    return app

app = create_app()
