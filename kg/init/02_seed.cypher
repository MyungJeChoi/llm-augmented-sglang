// 02_seed.cypher : Minimal KG seed (Terms, Synonyms, Table/Column mapping)

MERGE (t1:Term {term_id:'T_DOWNTIME', text:'다운타임', canonical:true})
MERGE (t1s:Term {term_id:'T_STOP', text:'정지시간', canonical:false})
MERGE (t1s)-[:SYNONYM_OF]->(t1);

MERGE (t2:Term {term_id:'T_LINE', text:'라인', canonical:true})
MERGE (t2s:Term {term_id:'T_LINEID', text:'라인ID', canonical:false})
MERGE (t2s)-[:SYNONYM_OF]->(t2);

MERGE (tbl:Table {name:'events'})
MERGE (c1:Column {col_id:'events.event_type', table:'events', name:'event_type'})
MERGE (c2:Column {col_id:'events.start_ts', table:'events', name:'start_ts'})
MERGE (c3:Column {col_id:'events.end_ts', table:'events', name:'end_ts'})
MERGE (c4:Column {col_id:'assets.location', table:'assets', name:'location'});

MERGE (t1)-[:MAPS_TO]->(c1);
MERGE (t2)-[:MAPS_TO]->(c4);

// Context graph: "다운타임" is related to "events" concept
MERGE (t1)-[:RELATED_TO]->(tbl);
