MATCH p=(a:Account {acct_no:$account,_workspace_id:$workspace})-[:TRANSFER*1..1]->(b:Account)
WHERE all(n IN nodes(p) WHERE n._workspace_id=$workspace) AND b<>a
RETURN b.acct_no AS account,min(length(p)) AS min_hops ORDER BY account LIMIT $limit
