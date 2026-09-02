def test_domain_surface_is_closed():
    import aurono.domain as d

    expected = {
        "types",
        "reasons",
        "math",
        "sizing",
        "strategy_eval",
        "decision_outcomes",
        "decision_builder",
        "events",
        "event_builder",
    }

    actual = {
        name for name in dir(d)
        if not name.startswith("_")
    }

    for name in expected:
        assert name in actual
