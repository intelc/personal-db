def test_tmp_root_fixture(tmp_root):
    assert tmp_root.exists()
    assert (tmp_root / "trackers").exists()
    assert (tmp_root / "entities").exists()
    assert (tmp_root / "notes").exists()
    assert (tmp_root / "state").exists()


def test_transforms_sdk_shim_importable():
    """Custom trackers import the flat SDK path; it must keep re-exporting
    core.transforms (a live custom tracker broke when this shim was missing)."""
    from personal_db.transforms import transform  # noqa: F401
