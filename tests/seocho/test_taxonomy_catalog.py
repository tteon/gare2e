from seocho.taxonomy_catalog import TaxonomyCatalog, TaxonomyConcept


def test_resolution_is_scoped_and_never_silently_merges_senses() -> None:
    catalog = TaxonomyCatalog([
        TaxonomyConcept("ml:svm", "Support Vector Machine", ("SVM",), facets={"topic": ("machine learning",)}),
        TaxonomyConcept("arch:svm", "Secure Virtual Machine", ("SVM",), facets={"topic": ("computer architecture",)}),
    ], scheme_id="urn:test:taxonomy")

    assert catalog.resolve("SVM")["status"] == "ambiguous"
    result = catalog.resolve("SVM", facets={"topic": "machine learning"})
    assert result["status"] == "resolved"
    assert result["concept"]["concept_id"] == "ml:svm"
    assert catalog.resolve("unknown")["status"] == "not_found"
    assert catalog.build_query_intent("SVM")["disposition"] == "abstain"
    intent = catalog.build_query_intent("SVM", facets={"topic": "machine learning"})
    assert intent["disposition"] == "ready"
    assert intent["resolved_anchor_ids"] == ["ml:svm"]
    assert catalog.text2cypher_arguments(intent)["disposition"] == "abstain"
    graph_catalog = TaxonomyCatalog([TaxonomyConcept("graph:svm", "Support Vector Machine", ("SVM",), facets={"graph_label": ("Concept",), "graph_identity": ("svm",)})], scheme_id="urn:test:graph")
    assert graph_catalog.text2cypher_arguments(graph_catalog.build_query_intent("SVM"))["anchor_label"] == "Concept"
