MATCH (a:Account {_workspace_id:$workspace,acct_no:$account})<-[r:SIGN_IN]-(m:Medium)
WHERE m._workspace_id=$workspace AND r.ts >= $start_ts AND r.ts < $end_ts
WITH DISTINCT a,m
MATCH (m)-[s:SIGN_IN]->(b:Account)
WHERE b._workspace_id=$workspace AND a<>b AND s.ts >= $start_ts AND s.ts < $end_ts
RETURN b.acct_no AS account,count(DISTINCT m) AS shared_media ORDER BY account LIMIT $limit
