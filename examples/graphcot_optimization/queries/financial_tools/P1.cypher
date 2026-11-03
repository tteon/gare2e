MATCH (a:Account {_workspace_id:$workspace, acct_no:$account})-[r:TRANSFER]->(b:Account)-[s:TRANSFER]->(c:Account)
WHERE b._workspace_id=$workspace AND c._workspace_id=$workspace
AND a<>b AND a<>c AND b<>c
AND r.amount >= $min_amount AND s.amount >= $min_amount
AND r.ts >= $start_ts AND r.ts < $end_ts AND s.ts >= $start_ts AND s.ts < $end_ts
AND s.ts >= r.ts AND s.ts-r.ts <= $max_gap_seconds
RETURN DISTINCT a.acct_no AS source,b.acct_no AS intermediary,c.acct_no AS destination
ORDER BY source,intermediary,destination LIMIT $limit
