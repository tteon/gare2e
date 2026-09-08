# Difficulty strata

## GTSQA
- rows: 1622
- strata: {'easy': 150, 'medium': 945, 'hard': 527}
- by hops: {(1, 'easy'): 150, (1, 'medium'): 371, (2, 'hard'): 195, (2, 'medium'): 406, (3, 'hard'): 282, (3, 'medium'): 168, (4, 'hard'): 50}
- test_type × stratum: {(('in_distribution',), 'easy'): 50, (('in_distribution',), 'hard'): 5, (('in_distribution',), 'medium'): 245, (('unseen_graph_type',), 'hard'): 476, (('unseen_graph_type',), 'medium'): 243, (('unseen_graph_type', 'unseen_relation_type'), 'hard'): 38, (('unseen_graph_type', 'unseen_relation_type'), 'medium'): 10, (('unseen_relation_type',), 'easy'): 100, (('unseen_relation_type',), 'hard'): 8, (('unseen_relation_type',), 'medium'): 447}

## GRBench MAPLE/Physics
- rows: 140
- strata: {'easy': 42, 'medium': 53, 'hard': 45}
- families: authors_of_paper=27 (easy), citation_count=20 (medium), venue_of_paper=15 (easy), coauthor_on_paper=10 (medium), author_of_both_papers=10 (medium), closest_collaborator=10 (hard), collaborators_in_year=10 (hard), joint_paper_count=10 (medium), most_cited_paper_of_author=10 (hard), paper_recommendation=10 (hard), venue_of_collaboration=5 (hard), author_paper_count=3 (medium)

