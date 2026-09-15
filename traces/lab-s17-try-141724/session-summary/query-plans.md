| pass | arm | group | replays | anchor kinds | expand share | db hits p50 / p90 | pc misses p50 / p90 | server ms p50 / p90 | findings |
|---|---|---|---|---|---|---|---|---|---|
| profile-b0-medium-k8 | B0 | stratum=medium | 89 | NodeIndexSeek 48, NodeIndexContainsScan 41 | 0.30 | 13.0 / 250.0 | 0.0 / 0.0 | 13.0 / 18.0 | cardinality_misestimation 73, eager_aggregation 17 |
| profile-s1-k8 | S1 | stratum=easy | 72 | NodeIndexSeek 51, NodeIndexContainsScan 21 | 0.42 | 41.0 / 292.0 | 0.0 / 0.0 | 19.0 / 31.0 | cardinality_misestimation 23, sort_or_top 10, eager_aggregation 8, variable_length_expansion 1 |
| profile-s1-k8 | S1 | stratum=hard | 99 | NodeIndexSeek 83, NodeIndexContainsScan 16 | 0.72 | 54.0 / 925.0 | 0.0 / 0.0 | 14.0 / 25.0 | cardinality_misestimation 52, eager_aggregation 41, sort_or_top 23 |
| profile-s1-k8 | S1 | stratum=medium | 57 | NodeIndexSeek 50, NodeIndexContainsScan 7 | 0.72 | 90.0 / 460.0 | 0.0 / 0.0 | 13.0 / 20.0 | cardinality_misestimation 30, eager_aggregation 20 |
| profile-b0-medium-k8 | B0 | family=author_of_both_papers | 26 | NodeIndexContainsScan 18, NodeIndexSeek 8 | 0.15 | 10.0 / 105.0 | 0.0 / 0.0 | 11.0 / 14.0 | cardinality_misestimation 24 |
| profile-b0-medium-k8 | B0 | family=author_paper_count | 6 | NodeIndexSeek 5, NodeIndexContainsScan 1 | 0.50 | 31.0 / 101.0 | 0.0 / 0.0 | 17.0 / 21.0 | eager_aggregation 4, cardinality_misestimation 3 |
| profile-b0-medium-k8 | B0 | family=citation_count | 20 | NodeIndexSeek 14, NodeIndexContainsScan 6 | 0.50 | 75.0 / 353.0 | 0.0 / 0.0 | 12.0 / 19.0 | cardinality_misestimation 19, eager_aggregation 10 |
| profile-b0-medium-k8 | B0 | family=coauthor_on_paper | 23 | NodeIndexContainsScan 14, NodeIndexSeek 9 | 0.30 | 13.0 / 126.0 | 0.0 / 0.0 | 15.0 / 20.0 | cardinality_misestimation 21 |
| profile-b0-medium-k8 | B0 | family=joint_paper_count | 14 | NodeIndexSeek 12, NodeIndexContainsScan 2 | 0.21 | 46.0 / 1268.0 | 0.0 / 0.0 | 14.0 / 22.0 | cardinality_misestimation 6, eager_aggregation 3 |
| profile-s1-k8 | S1 | family=author_of_both_papers | 18 | NodeIndexSeek 14, NodeIndexContainsScan 4 | 0.72 | 157.0 / 410.0 | 0.0 / 0.0 | 13.0 / 20.0 | cardinality_misestimation 8 |
| profile-s1-k8 | S1 | family=author_paper_count | 5 | NodeIndexSeek 3, NodeIndexContainsScan 2 | 0.80 | 10.0 / 117.0 | 0.0 / 0.0 | 17.0 / 20.0 | cardinality_misestimation 5, eager_aggregation 4 |
| profile-s1-k8 | S1 | family=authors_of_paper | 39 | NodeIndexSeek 33, NodeIndexContainsScan 6 | 0.31 | 63.0 / 4330.0 | 0.0 / 0.0 | 19.0 / 31.0 | cardinality_misestimation 12, sort_or_top 6, eager_aggregation 2, variable_length_expansion 1 |
| profile-s1-k8 | S1 | family=citation_count | 20 | NodeIndexSeek 19, NodeIndexContainsScan 1 | 0.50 | 90.0 / 493.0 | 0.0 / 0.0 | 11.0 / 17.0 | eager_aggregation 11, cardinality_misestimation 10 |
| profile-s1-k8 | S1 | family=closest_collaborator | 32 | NodeIndexSeek 29, NodeIndexContainsScan 3 | 0.75 | 100.0 / 1147.0 | 0.0 / 0.0 | 17.0 / 25.0 | eager_aggregation 21, cardinality_misestimation 14, sort_or_top 14 |
| profile-s1-k8 | S1 | family=coauthor_on_paper | 9 | NodeIndexSeek 9 | 1.00 | 99.0 / 223.0 | 0.0 / 0.0 | 14.0 / 20.0 | cardinality_misestimation 4 |
| profile-s1-k8 | S1 | family=collaborators_in_year | 27 | NodeIndexSeek 22, NodeIndexContainsScan 5 | 0.78 | 55.0 / 6525.0 | 0.0 / 0.0 | 12.0 / 24.0 | cardinality_misestimation 14, eager_aggregation 10 |
| profile-s1-k8 | S1 | family=joint_paper_count | 5 | NodeIndexSeek 5 | 1.00 | 166.0 / 832.0 | 0.0 / 0.0 | 27.0 / 33.0 | eager_aggregation 5, cardinality_misestimation 3 |
| profile-s1-k8 | S1 | family=most_cited_paper_of_author | 17 | NodeIndexSeek 14, NodeIndexContainsScan 3 | 0.71 | 54.0 / 175.0 | 0.0 / 0.0 | 13.0 / 26.0 | cardinality_misestimation 10, eager_aggregation 5, sort_or_top 5 |
| profile-s1-k8 | S1 | family=paper_recommendation | 11 | NodeIndexSeek 8, NodeIndexContainsScan 3 | 0.55 | 40.0 / 465.0 | 0.0 / 0.0 | 14.0 / 19.0 | cardinality_misestimation 8 |
| profile-s1-k8 | S1 | family=venue_of_collaboration | 12 | NodeIndexSeek 10, NodeIndexContainsScan 2 | 0.67 | 21.0 / 84.0 | 0.0 / 0.0 | 14.0 / 26.0 | cardinality_misestimation 6, eager_aggregation 5, sort_or_top 4 |
| profile-s1-k8 | S1 | family=venue_of_paper | 33 | NodeIndexSeek 18, NodeIndexContainsScan 15 | 0.55 | 41.0 / 103.0 | 0.0 / 0.0 | 18.0 / 33.0 | cardinality_misestimation 11, eager_aggregation 6, sort_or_top 4 |

**Replay errors:** none
