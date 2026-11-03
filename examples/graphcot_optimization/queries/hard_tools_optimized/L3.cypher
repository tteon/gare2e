MATCH (a:Account {_workspace_id:$workspace,acct_no:$account})-[r:TRANSFER]->(b)
WHERE b._workspace_id=$workspace AND a<>b AND r.amount >= $min_amount AND r.ts >= $start_ts AND r.ts < $end_ts
WITH DISTINCT a,b,r.ts AS first_ts
MATCH (b)-[s:TRANSFER]->(c)
WHERE c._workspace_id=$workspace AND c<>a AND c<>b AND s.amount >= $min_amount AND s.ts >= $start_ts AND s.ts < $end_ts AND s.ts >= first_ts
WITH DISTINCT a,b,c,s.ts AS second_ts
MATCH (c)-[t:TRANSFER]->(d:Account)
WHERE d._workspace_id=$workspace AND d<>a AND d<>b AND d<>c AND t.amount >= $min_amount AND t.ts >= $start_ts AND t.ts < $end_ts AND t.ts >= second_ts
RETURN DISTINCT [a.acct_no,b.acct_no,c.acct_no,d.acct_no] AS accounts ORDER BY accounts LIMIT $limit
