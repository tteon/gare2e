MATCH (a:Account {acct_no:$account,_workspace_id:$workspace})-[r:TRANSFER]->(b:Account {_workspace_id:$workspace})
WHERE r.amount >= $min_amount
RETURN DISTINCT b.acct_no AS account ORDER BY account LIMIT $limit
