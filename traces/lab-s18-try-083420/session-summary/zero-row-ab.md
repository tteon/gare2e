| pass | stratum | cases | answered | exhausted | queries | zero_row_events | cases_with_zero | recovered_after_zero | empty_retries | repairs | turns_p50 | wall_s_p50 | db_ms_p50 | hint_events | hint_resolved | hint_ms_p50 | hint_reads_p50 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hint-off-s1-k8 | easy | 30 | 18 | 12 | 90 | 16 | 11 | 14 | 36 | 8 | 4.5 | 10.3 | 149.0 | 0 | 0 | None | None |
| hint-off-s1-k8 | medium | 30 | 29 | 1 | 56 | 2 | 1 | 2 | 2 | 3 | 4.0 | 6.2 | 78.2 | 0 | 0 | None | None |
| hint-off-s1-k8 | hard | 30 | 5 | 24 | 129 | 40 | 19 | 31 | 55 | 32 | 6.0 | 18.9 | 277.5 | 0 | 0 | None | None |
| hint-on-s1-k8 | easy | 30 | 17 | 13 | 90 | 25 | 11 | 15 | 37 | 12 | 4.5 | 9.2 | 34.1 | 25 | 16 | 8.6 | 2 |
| hint-on-s1-k8 | medium | 30 | 28 | 2 | 60 | 3 | 2 | 3 | 4 | 4 | 4.0 | 6.5 | 15.5 | 3 | 3 | 12.0 | 4 |
| hint-on-s1-k8 | hard | 30 | 9 | 21 | 125 | 34 | 19 | 23 | 51 | 35 | 6.0 | 18.8 | 169.9 | 34 | 30 | 5.2 | 2.0 |

**hint kinds (pass, stratum):** hint-on-s1-k8/easy resolved=16,unresolved=9; hint-on-s1-k8/hard resolved=30,unresolved=4; hint-on-s1-k8/medium resolved=3

| pass | family | cases | answered | zero_row_events | recovered_after_zero |
|---|---|---|---|---|---|
| hint-off-s1-k8 | author_of_both_papers | 6 | 5 | 2 | 2 |
| hint-on-s1-k8 | author_of_both_papers | 6 | 4 | 3 | 3 |
| hint-off-s1-k8 | author_paper_count | 3 | 3 | 0 | 0 |
| hint-on-s1-k8 | author_paper_count | 3 | 3 | 0 | 0 |
| hint-off-s1-k8 | authors_of_paper | 17 | 11 | 9 | 8 |
| hint-on-s1-k8 | authors_of_paper | 17 | 10 | 13 | 11 |
| hint-off-s1-k8 | citation_count | 10 | 10 | 0 | 0 |
| hint-on-s1-k8 | citation_count | 10 | 10 | 0 | 0 |
| hint-off-s1-k8 | closest_collaborator | 7 | 1 | 10 | 10 |
| hint-on-s1-k8 | closest_collaborator | 7 | 5 | 12 | 6 |
| hint-off-s1-k8 | coauthor_on_paper | 6 | 6 | 0 | 0 |
| hint-on-s1-k8 | coauthor_on_paper | 6 | 6 | 0 | 0 |
| hint-off-s1-k8 | collaborators_in_year | 7 | 2 | 12 | 8 |
| hint-on-s1-k8 | collaborators_in_year | 7 | 2 | 9 | 8 |
| hint-off-s1-k8 | joint_paper_count | 5 | 5 | 0 | 0 |
| hint-on-s1-k8 | joint_paper_count | 5 | 5 | 0 | 0 |
| hint-off-s1-k8 | most_cited_paper_of_author | 7 | 0 | 10 | 10 |
| hint-on-s1-k8 | most_cited_paper_of_author | 7 | 0 | 7 | 5 |
| hint-off-s1-k8 | paper_recommendation | 6 | 2 | 4 | 2 |
| hint-on-s1-k8 | paper_recommendation | 6 | 2 | 2 | 0 |
| hint-off-s1-k8 | venue_of_collaboration | 3 | 0 | 4 | 1 |
| hint-on-s1-k8 | venue_of_collaboration | 3 | 0 | 4 | 4 |
| hint-off-s1-k8 | venue_of_paper | 13 | 7 | 7 | 6 |
| hint-on-s1-k8 | venue_of_paper | 13 | 7 | 12 | 4 |
