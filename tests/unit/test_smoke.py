def test_tmp_root_fixture(tmp_root):
    assert tmp_root.exists()
    assert (tmp_root / "trackers").exists()
    assert (tmp_root / "entities").exists()
    assert (tmp_root / "notes").exists()
    assert (tmp_root / "state").exists()
