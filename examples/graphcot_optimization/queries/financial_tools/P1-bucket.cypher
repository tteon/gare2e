MATCH (a:Account {_workspace_id:$workspace,acct_no:$account})-[:HAS_TRANSFER_BUCKET]->(ab:TransferBucket)
WHERE ab._workspace_id=$workspace AND ab.start_ts < $end_ts AND ab.end_ts > $start_ts
MATCH (ab)-[r:BUCKET_TRANSFER]->(b:Account)
WHERE b._workspace_id=$workspace AND a<>b AND r.amount >= $min_amount
AND r.ts >= $start_ts AND r.ts < $end_ts
MATCH (b)-[:HAS_TRANSFER_BUCKET]->(bb:TransferBucket)
WHERE bb._workspace_id=$workspace AND bb.start_ts <= r.ts+$max_gap_seconds AND bb.end_ts > r.ts
MATCH (bb)-[s:BUCKET_TRANSFER]->(c:Account)
WHERE c._workspace_id=$workspace AND a<>c AND b<>c
AND s.amount >= $min_amount AND s.ts >= $start_ts AND s.ts < $end_ts
AND s.ts >= r.ts AND s.ts-r.ts <= $max_gap_seconds
RETURN DISTINCT a.acct_no AS source,b.acct_no AS intermediary,c.acct_no AS destination
ORDER BY source,intermediary,destination LIMIT $limit
