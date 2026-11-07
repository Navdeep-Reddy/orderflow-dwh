-- Operational view: every source load with counts, status and failure reason.
SELECT r.run_id, r.status AS run_status, s.source_name, s.status AS source_status,
       s.rows_read, s.rows_accepted, s.rows_rejected, s.rows_inserted_new, s.rows_already_present,
       s.duration_seconds, COALESCE(s.failure_reason, '') AS failure_reason
  FROM pipeline_run r JOIN run_source s USING (run_id)
 ORDER BY r.started_at, s.source_name;
