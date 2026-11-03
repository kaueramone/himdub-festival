"""Instancias partilhadas (evita import circular entre app.py e os blueprints)."""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
