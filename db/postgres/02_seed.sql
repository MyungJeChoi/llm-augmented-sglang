-- 02_seed.sql : Minimal seed data (orgs, assets, events, metrics)

INSERT INTO orgs(org_name, parent_org_id) VALUES
  ('HQ', NULL),
  ('Manufacturing', 1),
  ('R&D', 1)
ON CONFLICT DO NOTHING;

INSERT INTO assets(asset_type, asset_name, org_id, location, tags) VALUES
  ('equipment', 'ETCH-01', 2, 'LINE-A', '{"model":"E100","vendor":"ACME"}'),
  ('equipment', 'ETCH-02', 2, 'LINE-A', '{"model":"E100","vendor":"ACME"}'),
  ('vehicle', 'CAR-01', 1, 'FLEET', '{"model":"S1"}')
ON CONFLICT DO NOTHING;

-- Events: downtime for equipment, alert for vehicle
INSERT INTO events(asset_id, event_type, start_ts, end_ts, severity, metadata) VALUES
  (1, 'downtime', now() - interval '3 day', now() - interval '3 day' + interval '2 hour', 2, '{"cause":"pump"}'),
  (1, 'maintenance', now() - interval '2 day', now() - interval '2 day' + interval '1 hour', 1, '{"work_order":"WO-100"}'),
  (2, 'downtime', now() - interval '1 day', now() - interval '1 day' + interval '30 min', 1, '{"cause":"sensor"}'),
  (3, 'alert', now() - interval '12 hour', now() - interval '12 hour' + interval '5 min', 3, '{"type":"lane_departure"}')
ON CONFLICT DO NOTHING;

-- Metrics
INSERT INTO metrics(asset_id, ts, metric_name, value) VALUES
  (1, now() - interval '3 day', 'temperature', 80.0),
  (1, now() - interval '2 day', 'temperature', 85.0),
  (3, now() - interval '12 hour', 'fatigue_score', 0.72)
ON CONFLICT DO NOTHING;
