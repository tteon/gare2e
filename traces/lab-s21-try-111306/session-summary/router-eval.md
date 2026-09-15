
## router-v0-k8: cases=90 answered=56 abstained=0
  lanes: {'grbench-graphcot/B0': '26/35', 'grbench-single/O1': '10/20', 'grbench-single/S1': '20/35'}
  reasons: {'intent_lookup_resolved->graphcot_hops_b0': 35, 'intent_argmax->text2cypher_o1_rowcap50': 20, 'intent_lookup_multihop_or_unanchored->text2cypher_s1': 4, 'intent_set_intersection->text2cypher_s1': 13, 'intent_count->text2cypher_s1_short': 18}
  mentions/resolved: cases with >=1 mention 80  with >=1 resolved 80  none resolved 0
  degree_class_max: {'mid': 41, None: 10, 'low': 35, 'hub': 4}  analysis_ms p50 11.5 max 243.3
  by family answered/cases: {'author_of_both_papers': '3/6', 'author_paper_count': '2/3', 'authors_of_paper': '11/17', 'citation_count': '7/10', 'closest_collaborator': '2/7', 'coauthor_on_paper': '6/6', 'collaborators_in_year': '2/7', 'joint_paper_count': '5/5', 'most_cited_paper_of_author': '5/7', 'paper_recommendation': '3/6', 'venue_of_collaboration': '0/3', 'venue_of_paper': '10/13'}

## router-v0a-k8: cases=90 answered=58 abstained=0
  lanes: {'grbench-graphcot/B0': '27/35', 'grbench-single/O1': '11/20', 'grbench-single/S1': '20/35'}
  reasons: {'intent_lookup_resolved->graphcot_hops_b0': 35, 'intent_lookup_multihop_or_unanchored->text2cypher_s1': 4, 'intent_argmax->text2cypher_o1_rowcap50': 20, 'intent_set_intersection->text2cypher_s1': 13, 'intent_count->text2cypher_s1_short': 18}
  mentions/resolved: cases with >=1 mention 80  with >=1 resolved 80  none resolved 0
  degree_class_max: {'mid': 41, 'hub': 4, None: 10, 'low': 35}  analysis_ms p50 8.4 max 20.8
  by family answered/cases: {'author_of_both_papers': '5/6', 'author_paper_count': '2/3', 'authors_of_paper': '11/17', 'citation_count': '6/10', 'closest_collaborator': '2/7', 'coauthor_on_paper': '6/6', 'collaborators_in_year': '1/7', 'joint_paper_count': '5/5', 'most_cited_paper_of_author': '6/7', 'paper_recommendation': '3/6', 'venue_of_collaboration': '0/3', 'venue_of_paper': '11/13'}

## heldout-router-v0-k8: cases=50 answered=31 abstained=0
  lanes: {'grbench-graphcot/B0': '8/12', 'grbench-single/O1': '5/10', 'grbench-single/S1': '18/28'}
  reasons: {'intent_lookup_resolved->graphcot_hops_b0': 12, 'intent_argmax->text2cypher_o1_rowcap50': 10, 'intent_set_intersection->text2cypher_s1': 7, 'intent_count->text2cypher_s1_short': 19, 'intent_lookup_multihop_or_unanchored->text2cypher_s1': 2}
  mentions/resolved: cases with >=1 mention 46  with >=1 resolved 46  none resolved 0
  degree_class_max: {'low': 23, None: 4, 'mid': 20, 'hub': 3}  analysis_ms p50 7.8 max 12.0
  by family answered/cases: {'author_of_both_papers': '4/4', 'author_paper_count': '1/4', 'authors_of_paper': '3/6', 'citation_count': '9/10', 'closest_collaborator': '2/3', 'coauthor_on_paper': '4/4', 'collaborators_in_year': '1/3', 'joint_paper_count': '3/5', 'most_cited_paper_of_author': '3/3', 'paper_recommendation': '0/4', 'venue_of_collaboration': '0/2', 'venue_of_paper': '1/2'}

## heldout-s1-k8 (S1 comparator): answered=34/50
  by family: {'author_of_both_papers': '4/4', 'author_paper_count': '4/4', 'authors_of_paper': '5/6', 'citation_count': '10/10', 'closest_collaborator': '1/3', 'coauthor_on_paper': '4/4', 'collaborators_in_year': '1/3', 'joint_paper_count': '5/5', 'most_cited_paper_of_author': '0/3', 'paper_recommendation': '0/4', 'venue_of_collaboration': '0/2', 'venue_of_paper': '0/2'}

## m27b-s1-k8 (S1 comparator): answered=56/90
  by family: {'author_of_both_papers': '5/6', 'author_paper_count': '3/3', 'authors_of_paper': '12/17', 'citation_count': '10/10', 'closest_collaborator': '3/7', 'coauthor_on_paper': '6/6', 'collaborators_in_year': '2/7', 'joint_paper_count': '5/5', 'most_cited_paper_of_author': '0/7', 'paper_recommendation': '3/6', 'venue_of_collaboration': '0/3', 'venue_of_paper': '7/13'}
