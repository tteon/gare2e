MATCH (a:Account {_workspace_id:$workspace})-[r:TRANSFER]->(b:Account {_workspace_id:$workspace})
WHERE a<>b AND r.ts >= $start_ts AND r.ts < $end_ts
WITH b,count(DISTINCT a) AS senders,sum(r.amount) AS total_amount
WHERE senders >= $min_senders
RETURN b.acct_no AS account,senders,total_amount ORDER BY account LIMIT $limit
