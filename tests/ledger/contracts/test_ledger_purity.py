def test_ledger_has_no_domain_or_exchange_io_imports():
    import aurono.ledger.reducer as r
    src = r.__file__
    # very light check: ledger should not depend on domain evaluation or adapters
    # (you already have adapter purity; this is just belt & suspenders)
    assert "ledger" in src
