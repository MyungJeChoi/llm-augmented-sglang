"""Seed Neo4j for the scaffold (constraints + minimal terms/column mappings).

Run:
    python api/scripts/seed_neo4j.py

It reads Neo4j credentials from repo_root/.env via app.core.config.Settings.

This script is safe to run multiple times:
- constraints use IF NOT EXISTS
- data uses MERGE
"""
from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "api"
sys.path.insert(0, str(API_DIR))

from neo4j import GraphDatabase  # noqa: E402
from app.core.config import settings  # noqa: E402

def _split_cypher_statements(text: str) -> list[str]:
    # naive split on ';' (good enough for our small seed files)
    parts = [p.strip() for p in text.split(";")]
    return [p for p in parts if p and not p.startswith("//")]

def run_file(session, path: Path):
    content = path.read_text(encoding="utf-8")
    # remove full-line comments
    lines = []
    for line in content.splitlines():
        if line.strip().startswith("//") or line.strip().startswith("#"):
            continue
        lines.append(line)
    content2 = "\n".join(lines)
    statements = _split_cypher_statements(content2)
    for stmt in statements:
        session.run(stmt)

def main():
    init_dir = REPO_ROOT / "kg" / "init"
    files = [
        init_dir / "01_constraints.cypher",
        init_dir / "02_seed.cypher",
    ]
    for f in files:
        if not f.exists():
            raise FileNotFoundError(f"Missing cypher seed file: {f}")

    print(f"[seed_neo4j] connecting to: {settings.neo4j_bolt_url} as {settings.neo4j_user}")
    driver = GraphDatabase.driver(settings.neo4j_bolt_url, auth=(settings.neo4j_user, settings.neo4j_password))
    try:
        with driver.session() as s:
            for f in files:
                print(f"[seed_neo4j] running: {f.name}")
                run_file(s, f)
        print("[seed_neo4j] done.")
    finally:
        driver.close()

if __name__ == "__main__":
    main()
