from benchmarks.optimize import execute, make_fixture, nearby_configs, parse_configs


def test_speed_trial_rejects_wrong_content():
    server, thread, reports, golden = make_fixture(2, 0, 0)
    try:
        good = execute(reports, golden, (2, 2), allow_http=True)
        assert good["accuracy_ok"]
        wrong = dict(golden)
        first = next(iter(wrong))
        wrong[first] = ("0" * 64, 1)
        bad = execute(reports, wrong, (2, 2), allow_http=True)
        assert not bad["accuracy_ok"]
        assert any("SHA-256 mismatch" in error for error in bad["errors"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_nearby_settings_respect_host_cap():
    assert parse_configs("4:2,4:2") == [(4, 2)]
    choices = nearby_configs({"workers": 32, "per_host": 2}, "real")
    assert all(per_host <= 2 for _, per_host in choices)
