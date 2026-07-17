import pytest

from personal_db.core.entrypoints import load_entrypoint, load_module_from_file


def test_load_module_from_file_execs_and_registers(tmp_path):
    mod_path = tmp_path / "thing.py"
    mod_path.write_text("VALUE = 42\n")
    module = load_module_from_file(mod_path, "test_load_module_from_file_execs_and_registers")
    assert module.VALUE == 42


def test_load_module_from_file_reloads_on_edit(tmp_path):
    mod_path = tmp_path / "thing.py"
    modname = "test_load_module_from_file_reloads_on_edit"
    mod_path.write_text("VALUE = 1\n")
    first = load_module_from_file(mod_path, modname)
    assert first.VALUE == 1
    mod_path.write_text("VALUE = 2\n")
    second = load_module_from_file(mod_path, modname)
    assert second.VALUE == 2


def test_load_entrypoint_resolves_module_and_function(tmp_path):
    (tmp_path / "jobs.py").write_text("def run(cfg):\n    return cfg\n")
    func = load_entrypoint(tmp_path, "jobs:run", modname_prefix="test_prefix")
    assert func("sentinel") == "sentinel"


def test_load_entrypoint_rejects_malformed_entrypoint(tmp_path):
    with pytest.raises(ValueError):
        load_entrypoint(tmp_path, "no_colon_here", modname_prefix="test_prefix")


def test_load_entrypoint_missing_module_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_entrypoint(tmp_path, "missing:run", modname_prefix="test_prefix")


def test_load_entrypoint_missing_function_raises(tmp_path):
    (tmp_path / "jobs.py").write_text("def run(cfg):\n    return cfg\n")
    with pytest.raises(AttributeError):
        load_entrypoint(tmp_path, "jobs:nope", modname_prefix="test_prefix")
