MATCH p=(a:Account {acct_no:$account,_workspace_id:$workspace})-[:TRANSFER*3]->(b:Account)
WHERE all(n IN nodes(p) WHERE n._workspace_id=$workspace)
 AND all(n IN nodes(p) WHERE single(m IN nodes(p) WHERE m=n))
 AND all(r IN relationships(p) WHERE r.amount >= $min_amount AND r.ts >= $start_ts AND r.ts < $end_ts)
 AND all(i IN range(0,length(p)-2) WHERE relationships(p)[i].ts <= relationships(p)[i+1].ts)
RETURN DISTINCT [n IN nodes(p)|n.acct_no] AS accounts ORDER BY accounts LIMIT $limit
