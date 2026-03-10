// 01_constraints.cypher : Constraints & Indexes

CREATE CONSTRAINT term_id IF NOT EXISTS FOR (t:Term) REQUIRE t.term_id IS UNIQUE;
CREATE CONSTRAINT table_name IF NOT EXISTS FOR (t:Table) REQUIRE t.name IS UNIQUE;
CREATE CONSTRAINT column_id IF NOT EXISTS FOR (c:Column) REQUIRE c.col_id IS UNIQUE;

CREATE INDEX term_text IF NOT EXISTS FOR (t:Term) ON (t.text);
CREATE INDEX column_name IF NOT EXISTS FOR (c:Column) ON (c.name);
