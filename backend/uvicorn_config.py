"""
Uvicorn configuration for development
Excludes .venv and other unnecessary directories from file watching
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["app"],  # Only watch app directory, exclude .venv
        reload_excludes=["*.pyc", "__pycache__", ".venv/*", "*.log"]
    )
