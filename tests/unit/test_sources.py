import pytest
import yaml

from personal_db.core.config import Config
from personal_db.core.sources import (
    SourceManifestError,
    discover_sources,
    install_source_template,
    list_bundled_sources,
    load_source_manifest,
    update_source_template,
)


def test_list_bundled_sources_includes_spark_email():
    assert "spark_email" in list_bundled_sources()


def test_install_source_template_copies_source_yaml(tmp_root):
    cfg = Config(root=tmp_root)

    dest = install_source_template(cfg, "spark_email")

    assert dest == tmp_root / "sources" / "spark_email"
    assert (dest / "source.yaml").exists()
    assert (dest / "instructions.md").exists()
    sources = discover_sources(cfg)
    assert sources["spark_email"].manifest.provider == "spark"


def test_update_source_template_preserves_extra_files(tmp_root):
    cfg = Config(root=tmp_root)
    dest = install_source_template(cfg, "spark_email")
    extra = dest / "local_note.md"
    extra.write_text("keep")
    (dest / "source.yaml").write_text("name: stale\n")

    update_source_template(cfg, "spark_email")

    assert extra.read_text() == "keep"
    assert "provider: spark" in (dest / "source.yaml").read_text()


def test_load_source_manifest_rejects_bad_capabilities(tmp_path):
    path = tmp_path / "source.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "bad_source",
                "provider": "spark",
                "description": "bad",
                "capabilities": "search",
            }
        )
    )

    with pytest.raises(SourceManifestError):
        load_source_manifest(path)
