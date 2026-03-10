from __future__ import annotations

from neo4j import GraphDatabase


class KGClient:
    def __init__(self, bolt_url: str, user: str, password: str):
        self._driver = GraphDatabase.driver(bolt_url, auth=(user, password))

    def close(self):
        self._driver.close()

    # -----------------
    # Read helpers
    # -----------------

    def get_term_by_id(self, term_id: str) -> dict | None:
        q = "MATCH (t:Term {term_id:$term_id}) RETURN t LIMIT 1"
        with self._driver.session() as s:
            r = s.run(q, term_id=term_id).single()
            return dict(r["t"]) if r else None

    def get_term_by_text(self, text: str) -> dict | None:
        q = "MATCH (t:Term {text:$text}) RETURN t LIMIT 1"
        with self._driver.session() as s:
            r = s.run(q, text=text).single()
            return dict(r["t"]) if r else None

    def get_column(self, col_id: str) -> dict | None:
        q = "MATCH (c:Column {col_id:$col_id}) RETURN c LIMIT 1"
        with self._driver.session() as s:
            r = s.run(q, col_id=col_id).single()
            return dict(r["c"]) if r else None

    # -----------------
    # Write helpers (admin)
    # -----------------

    def upsert_term(self, term_id: str, text: str, canonical: bool):
        q = """
        MERGE (t:Term {term_id:$term_id})
        SET t.text=$text,
            t.canonical=$canonical
        RETURN t
        """
        with self._driver.session() as s:
            s.run(q, term_id=term_id, text=text, canonical=canonical)

    def upsert_synonym(self, alias_term_id: str, alias_text: str, canonical_term_id: str, canonical_text: str):
        """Ensure (alias)-[:SYNONYM_OF]->(canonical)."""

        q = """
        MERGE (alias:Term {term_id:$alias_term_id})
        SET alias.text=$alias_text,
            alias.canonical=false
        MERGE (can:Term {term_id:$canonical_term_id})
        SET can.text=$canonical_text,
            can.canonical=true
        MERGE (alias)-[:SYNONYM_OF]->(can)
        RETURN alias, can
        """
        with self._driver.session() as s:
            s.run(
                q,
                alias_term_id=alias_term_id,
                alias_text=alias_text,
                canonical_term_id=canonical_term_id,
                canonical_text=canonical_text,
            )

    def upsert_mapping(self, term_text: str, table: str, column: str):
        """Ensure (Term)-[:MAPS_TO]->(Column)."""

        col_id = f"{table}.{column}"
        q = """
        MERGE (t:Term {text:$term_text})
        ON CREATE SET t.term_id=$term_id, t.canonical=true
        MERGE (tbl:Table {name:$table})
        MERGE (c:Column {col_id:$col_id})
        SET c.table=$table, c.name=$column
        MERGE (t)-[:MAPS_TO]->(c)
        MERGE (t)-[:RELATED_TO]->(tbl)
        RETURN t, c
        """
        # Allocate a synthetic term_id on first insert (scaffold choice).
        # In production, you would use a proper ID allocation strategy (or enforce unique text).
        import uuid
        term_id = f"T_{uuid.uuid4().hex[:10]}"
        with self._driver.session() as s:
            s.run(q, term_text=term_text, term_id=term_id, table=table, col_id=col_id, column=column)

    # -----------------
    # Runtime features
    # -----------------

    def canonicalize(self, raw_text: str) -> str:
        """Return canonical term text if synonym exists; else return raw_text."""
        q = """
        MATCH (syn:Term {text:$raw})-[:SYNONYM_OF]->(can:Term {canonical:true})
        RETURN can.text AS canonical
        LIMIT 1
        """
        with self._driver.session() as s:
            r = s.run(q, raw=raw_text).single()
            return r["canonical"] if r else raw_text

    def map_terms_to_columns(self, terms: list[str]) -> list[dict]:
        """Return candidate Column mappings for input terms.

        Supports both:
        - direct mapping: (Term {text in terms})-[:MAPS_TO]->(Column)
        - synonym mapping: (alias {text in terms})-[:SYNONYM_OF]->(canonical)-[:MAPS_TO]->(Column)
        """

        # 처리 흐름
        # 1) terms 배열을 tok 단위로 펼친 뒤 각 토큰에 대해 처리한다.
        # 2) tok 텍스트를 가진 Term(t) 를 찾는다.
        # 3) tok 텍스트를 가진 Term이 SYNONYM_OF로 canonical=true Term(t2)에 연결되면 t2를, 없으면 t를 사용한다.
        # 4) 최종 term이 MAPS_TO 관계로 연결된 Column(c)을 찾아 매핑한다.
        #    - term이 없거나 매핑이 없으면 해당 tok은 결과에서 제외된다.
        # 5) 반환되는 각 행은 아래 5개 필드를 가진다.
        #    raw: 입력 토큰 원문(tok)
        #    canonical: 최종 선택된 term.text (동의어면 canonical term)
        #    col_id: 매핑된 컬럼 식별자
        #    table: 매핑된 테이블명
        #    name: 매핑된 컬럼명
        # 6) 한 tok이 여러 컬럼에 매핑되면 여러 행이 생성될 수 있다.
        # 7) 전체 결과는 최대 100개 행까지만 반환한다.
        
        # 처리 예시
        # tok: "다운타임"
        # - 매칭: Term(text='다운타임', canonical=true)
        # - MAPS_TO -> (events.event_type)
        # - 결과: table=events, name=event_type, col_id=events.event_type
        
        q = """
        UNWIND $terms AS tok
        OPTIONAL MATCH (t:Term {text: tok})
        OPTIONAL MATCH (alias:Term {text: tok})-[:SYNONYM_OF]->(t2:Term {canonical:true})
        WITH tok, coalesce(t2, t) AS term
        MATCH (term)-[:MAPS_TO]->(c:Column)
        RETURN tok AS raw,
               term.text AS canonical,
               c.col_id AS col_id,
               c.table AS table,
               c.name AS name
        LIMIT 100
        """
        with self._driver.session() as s:
            return [dict(r) for r in s.run(q, terms=terms)]

    def get_term_neighbors(self, term_text: str, limit: int = 20) -> list[dict]:
        q = """
        MATCH (t:Term {text:$text})-[r]-(n)
        RETURN type(r) AS rel, labels(n) AS labels, n AS node
        LIMIT $limit
        """
        with self._driver.session() as s:
            out = []
            for r in s.run(q, text=term_text, limit=limit):
                node = r["node"]
                out.append({"rel": r["rel"], "labels": r["labels"], "node": dict(node)})
            return out

    # -----------------
    # Validation helpers
    # -----------------

    def validate(self, limit: int = 100) -> dict:
        """Return a small set of KG consistency checks."""

        checks = {}

        # Terms without mapping
        q1 = """
        MATCH (t:Term)
        WHERE NOT (t)-[:MAPS_TO]->(:Column)
        RETURN t.term_id AS term_id, t.text AS text, coalesce(t.canonical,false) AS canonical
        LIMIT $limit
        """

        # Synonyms pointing to non-canonical target
        q2 = """
        MATCH (s:Term)-[:SYNONYM_OF]->(t:Term)
        WHERE coalesce(t.canonical,false) <> true
        RETURN s.text AS synonym, t.text AS target
        LIMIT $limit
        """

        # Orphan columns (no term maps to them)
        q3 = """
        MATCH (c:Column)
        WHERE NOT (:Term)-[:MAPS_TO]->(c)
        RETURN c.col_id AS col_id, c.table AS table, c.name AS name
        LIMIT $limit
        """

        with self._driver.session() as s:
            checks["terms_without_mapping"] = [dict(r) for r in s.run(q1, limit=limit)]
            checks["synonyms_to_non_canonical"] = [dict(r) for r in s.run(q2, limit=limit)]
            checks["orphan_columns"] = [dict(r) for r in s.run(q3, limit=limit)]

        return checks
