MATCH (a:Account {_workspace_id:$workspace,acct_no:$account})-[r:TRANSFER]->(b:Account)
WHERE b._workspace_id=$workspace AND a<>b AND r.amount >= $min_amount AND r.ts >= $start_ts AND r.ts < $end_ts
WITH DISTINCT a,b,r.ts AS first_ts
MATCH (b)-[s:TRANSFER]->(c:Account)
WHERE c._workspace_id=$workspace AND a<>c AND b<>c AND s.amount >= $min_amount
 AND s.ts >= $start_ts AND s.ts < $end_ts AND s.ts >= first_ts AND s.ts-first_ts <= $max_gap_seconds
RETURN DISTINCT a.acct_no AS source,b.acct_no AS intermediary,c.acct_no AS destination
ORDER BY source,intermediary,destination LIMIT $limit
