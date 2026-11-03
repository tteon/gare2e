MATCH (b:Account {_workspace_id:$workspace})
CALL (b) {
 MATCH (a:Account {_workspace_id:$workspace})-[r:TRANSFER]->(b)
 WHERE a<>b AND r.ts >= $start_ts AND r.ts < $end_ts
 RETURN count(DISTINCT a) AS senders,sum(r.amount) AS total_amount
}
WITH b,senders,total_amount
WHERE senders >= $min_senders
RETURN b.acct_no AS account,senders,total_amount ORDER BY account LIMIT $limit
