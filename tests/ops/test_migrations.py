"""
The Alembic history is one line, and upgrading a fresh database to head gives exactly the schema the
models declare (alembic check). Runs on a throwaway SQLite file, never on the configured database.
"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from govcareeros.backend.config.settings import settings

ROOT = Path(__file__).parents[2]

def _config() -> Config:
    # No ini file: env.py would otherwise run logging.fileConfig and disable the loggers other tests read
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg

def test_single_head():
    assert len(ScriptDirectory.from_config(_config()).get_heads()) == 1

def test_upgrade_downgrade_upgrade_matches_models(tmp_path, monkeypatch):
    # alembic/env.py takes its URL from settings.DATABASE_URL
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'migrations.db').as_posix()}")
    cfg = _config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if autogenerate would emit any operation
