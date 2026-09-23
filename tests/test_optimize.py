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


def test_valid_run_can_still_miss_throughput_target(tmp_path, capsys):
    from benchmarks.optimize import main
    result = main(["fixture", "--count", "2", "--image-side", "0", "--configs", "2:2",
                   "--repeats", "2", "--target-rate", "1000000000",
                   "--history", str(tmp_path / "results.jsonl"),
                   "--champion", str(tmp_path / "champions.json")])
    assert result == 2
    assert '"target_met": false' in capsys.readouterr().out


def test_render_fingerprint_detects_graphic_changes_with_same_text(tmp_path):
    import fitz
    from benchmarks.render_cached import fingerprint
    fingerprints = []
    for name, color in (("red", (1, 0, 0)), ("blue", (0, 0, 1))):
        path = tmp_path / f"{name}.pdf"
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), "Annual report 2024")
            page.draw_rect(fitz.Rect(72, 100, 150, 150), fill=color)
            doc.save(path)
        fingerprints.append(fingerprint(path))
    assert fingerprints[0]["pages"] == fingerprints[1]["pages"]
    assert fingerprints[0]["text_sha256"] == fingerprints[1]["text_sha256"]
    assert fingerprints[0]["visual_36dpi_sha256"] != fingerprints[1]["visual_36dpi_sha256"]
