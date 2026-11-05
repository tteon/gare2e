MATCH (p)-[:GUARANTEE]->(q), (p)-[:OWN]->(a:Account), (q)-[:OWN]->(b:Account)
WHERE p<>q AND a.acct_no < b.acct_no AND a.acct_no >= $account_min AND a.acct_no <= $account_max
AND all(n IN [p,q,a,b] WHERE n._workspace_id=$workspace)
AND EXISTS { MATCH (a)-[:TRANSFER]->(b) }
AND EXISTS { MATCH (m:Medium {_workspace_id:$workspace})-[:SIGN_IN]->(a), (m)-[:SIGN_IN]->(b) }
AND NOT EXISTS { MATCH (a)-[:REPAY]->(:Loan {_workspace_id:$workspace}) }
AND NOT EXISTS { MATCH (b)-[:REPAY]->(:Loan {_workspace_id:$workspace}) }
WITH DISTINCT a,b
CALL (a,b) { MATCH (a)-[r:TRANSFER]-(b) RETURN sum(r.amount) AS total_amount }
RETURN a.acct_no AS account_a,b.acct_no AS account_b,total_amount
ORDER BY total_amount DESC,account_a,account_b LIMIT $top_k
