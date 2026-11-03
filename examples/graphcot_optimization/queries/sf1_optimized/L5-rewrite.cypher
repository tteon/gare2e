MATCH (p)-[:GUARANTEE]->(q), (p)-[:OWN]->(a:Account), (q)-[:OWN]->(b:Account)
WHERE p<>q AND a.acct_no < b.acct_no AND a.acct_no >= $account_min AND a.acct_no <= $account_max
AND all(n IN [p,q,a,b] WHERE n._workspace_id=$workspace)
AND EXISTS { MATCH (a)-[:TRANSFER]->(b) }
AND EXISTS { MATCH (m:Medium {_workspace_id:$workspace})-[:SIGN_IN]->(a), (m)-[:SIGN_IN]->(b) }
RETURN DISTINCT a.acct_no AS account_a,b.acct_no AS account_b ORDER BY account_a,account_b LIMIT $limit
