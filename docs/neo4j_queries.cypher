// Plant Memory: Cypher cookbook for Neo4j Browser / Bloom
// Every Plant Memory node carries the :PM label. Fact relationships carry
// fact_id, confidence, method, status (proposed | verified | rejected) and evidence.

// 1. The schema at a glance
CALL db.schema.visualization();

// 2. Dryer elevator: its failures and their causes, as a graph
MATCH (a:PM:Asset {asset_id: 'PARB-DRY-ELV-01'})<-[:ON_ASSET]-(e:PM:MaintenanceEvent)-[r:HAS_FAILURE_MODE|HAS_ROOT_CAUSE]->(t)
WHERE r.status <> 'rejected'
RETURN a, e, r, t LIMIT 150;

// 3. Bad actors: failure downtime by asset
MATCH (e:PM:MaintenanceEvent)-[f:HAS_FAILURE_MODE]->(:PM:FailureMode)
WHERE f.status <> 'rejected'
MATCH (e)-[:ON_ASSET]->(a:PM:Asset)
RETURN a.name AS asset, count(e) AS failures, sum(coalesce(e.downtime_hrs, 0)) AS downtime_hrs, sum(e.total_cost) AS cost_rs
ORDER BY downtime_hrs DESC LIMIT 10;

// 4. What causes what, where (failure mode x root cause per asset)
MATCH (a:PM:Asset)<-[:ON_ASSET]-(e:PM:MaintenanceEvent)-[f:HAS_FAILURE_MODE]->(fm:PM:FailureMode)
MATCH (e)-[c:HAS_ROOT_CAUSE]->(rc:PM:RootCause)
WHERE f.status <> 'rejected' AND c.status <> 'rejected'
RETURN a.name AS asset, fm.label AS failure_mode, rc.label AS root_cause, count(*) AS events,
       sum(CASE WHEN c.status = 'verified' THEN 1 ELSE 0 END) AS verified
ORDER BY events DESC LIMIT 25;

// 5. Spare parts shared across assets (design / standardisation signal)
MATCH (s:PM:SparePart)<-[:CONSUMED]-(e:PM:MaintenanceEvent)-[:ON_ASSET]->(a:PM:Asset)
RETURN s.description AS part, count(DISTINCT a) AS assets, collect(DISTINCT a.name) AS used_on, count(e) AS orders
ORDER BY assets DESC, orders DESC LIMIT 15;

// 6. Knowledge captured from technicians
MATCH (a:PM:Asset)-[r:HAS_ROOT_CAUSE {method: 'technician'}]->(c:PM:RootCause)
RETURN a.name AS asset, r.failure_mode AS failure_mode, c.label AS cause, r.status AS status, r.evidence_snippet AS statement;

// 7. Verification queue: lowest-confidence proposed facts first
MATCH (e:PM:MaintenanceEvent)-[r:INVOLVES_COMPONENT|OBSERVED_SYMPTOM|HAS_FAILURE_MODE|HAS_ROOT_CAUSE|RESOLVED_BY]->(t)
WHERE r.status = 'proposed'
RETURN r.fact_id AS fact, type(r) AS relation, r.object_id AS value, r.confidence AS confidence,
       e.description AS work_order, r.evidence_snippet AS evidence
ORDER BY confidence ASC LIMIT 20;

// 8. Ontology drift proposals and the work orders behind them
MATCH (d:PM:DriftProposal)-[:SEEN_IN]->(e:PM:MaintenanceEvent)
RETURN d, e;

// 9. Failure mode -> affected components -> spare parts that realise them
MATCH p = (fm:PM:FailureMode {id: 'bearing_wear'})-[:TYPICALLY_AFFECTS]->(:PM:Component)<-[:REALIZES]-(:PM:SparePart)
RETURN p;

// 10. Remove all Plant Memory data (leaves everything else in the database untouched)
// MATCH (n:PM) DETACH DELETE n;
