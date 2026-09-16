"""gunicorn entrypoint: gunicorn --bind 127.0.0.1:8100 wsgi:app"""
from app import create_app

app = create_app()
