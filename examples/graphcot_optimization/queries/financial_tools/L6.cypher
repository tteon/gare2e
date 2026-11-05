MATCH (a:Account {_workspace_id:$workspace})-[:TRANSFER]->(b:Account {_workspace_id:$workspace})
WHERE a.acct_no < b.acct_no AND a.acct_no >= $account_min AND a.acct_no <= $account_max
 AND EXISTS {
 MATCH (p)-[:OWN]->(a), (q)-[:OWN]->(b)
 WHERE p._workspace_id=$workspace AND q._workspace_id=$workspace AND p<>q
 AND EXISTS { MATCH (p)-[:GUARANTEE]->(q) }
 }
 AND EXISTS { MATCH (m:Medium {_workspace_id:$workspace})-[:SIGN_IN]->(a), (m)-[:SIGN_IN]->(b) }
 AND NOT EXISTS { MATCH (a)-[:REPAY]->(:Loan {_workspace_id:$workspace}) }
 AND NOT EXISTS { MATCH (b)-[:REPAY]->(:Loan {_workspace_id:$workspace}) }
WITH DISTINCT a,b
CALL (a,b) { MATCH (a)-[r:TRANSFER]-(b) RETURN sum(r.amount) AS total_amount }
RETURN a.acct_no AS account_a,b.acct_no AS account_b,total_amount
ORDER BY total_amount DESC,account_a,account_b LIMIT $top_k
