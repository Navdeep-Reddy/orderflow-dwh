-- Data-quality view: rejected records by run, source and reason (quarantine summary).
SELECT run_id, source_name, reason_code, COUNT(*) AS rejected_rows
  FROM reject_record
 GROUP BY run_id, source_name, reason_code
 ORDER BY run_id, source_name, rejected_rows DESC, reason_code;
