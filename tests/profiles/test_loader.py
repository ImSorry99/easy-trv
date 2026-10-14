from pathlib import Path

import pytest

from custom_components.easy_trv.profiles import loader

BUILTIN_TEXT = (loader.BUILTIN_DIR / "sonoff_trvzb_native.yaml").read_text()


def _user_profile(directory: Path, file: str, profile_id: str) -> None:
    text = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", f"id: {profile_id}", 1)
    (directory / file).write_text(text)


def test_builtin_loads() -> None:
    result = loader.load_profiles_sync(user_dir=None)
    assert set(result.profiles) == {"sonoff_trvzb_native"}
    assert result.errors == ()


def test_invalid_user_files_reported_and_skipped() -> None:
    result = loader.load_profiles_sync(Path("tests/profiles/invalid"))
    assert set(result.profiles) == {"sonoff_trvzb_native"}
    assert sorted(e.file for e in result.errors) == [
        "bad_encoder.yaml",
        "duplicate_intent.yaml",
    ]


def test_user_profile_without_set_source_is_a_load_error(tmp_path: Path) -> None:
    text = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: user_nosource", 1)
    start = text.index("  source_select:")
    end = text.index("  window_detection_flag:")
    (tmp_path / "nosource.yaml").write_text(text[:start] + text[end:])
    result = loader.load_profiles_sync(tmp_path)
    assert set(result.profiles) == {"sonoff_trvzb_native"}
    [error] = result.errors
    assert error.file == "nosource.yaml"
    assert "set_source" in str(error)


def test_user_id_collision_with_builtin_is_error() -> None:
    result = loader.load_profiles_sync(Path("tests/profiles/collision"))
    assert [e.file for e in result.errors] == ["sonoff_trvzb_native.yaml"]
    assert result.profiles["sonoff_trvzb_native"].name == "Sonoff TRVZB (native)"


def test_missing_user_dir_is_ignored() -> None:
    result = loader.load_profiles_sync(Path("tests/profiles/does_not_exist"))
    assert set(result.profiles) == {"sonoff_trvzb_native"}
    assert result.errors == ()


def test_valid_user_profile_is_loaded_next_to_builtin(tmp_path: Path) -> None:
    _user_profile(tmp_path, "mine.yaml", "user_mine")
    (tmp_path / "notes.txt").write_text("not a profile")
    result = loader.load_profiles_sync(tmp_path)
    assert set(result.profiles) == {"sonoff_trvzb_native", "user_mine"}
    assert result.errors == ()


def test_duplicate_user_id_keeps_the_first_file(tmp_path: Path) -> None:
    _user_profile(tmp_path, "a.yaml", "user_same")
    _user_profile(tmp_path, "b.yaml", "user_same")
    result = loader.load_profiles_sync(tmp_path)
    assert "user_same" in result.profiles
    assert [e.file for e in result.errors] == ["b.yaml"]


@pytest.mark.parametrize(
    "content",
    [
        "",
        "- a list\n- is not a profile\n",
        "key: [unclosed\n",
        "schema: 1\nid: Not A Slug\n",
    ],
)
def test_unusable_user_file_is_reported(tmp_path: Path, content: str) -> None:
    (tmp_path / "broken.yaml").write_text(content)
    result = loader.load_profiles_sync(tmp_path)
    assert set(result.profiles) == {"sonoff_trvzb_native"}
    assert [e.file for e in result.errors] == ["broken.yaml"]
    assert str(result.errors[0])


def test_profile_that_cannot_be_built_is_reported(tmp_path: Path) -> None:
    text = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: user_keys").replace(
        "internal: [0] }", "inside: [0] }"
    )
    (tmp_path / "keys.yaml").write_text(text)
    result = loader.load_profiles_sync(tmp_path)
    assert set(result.profiles) == {"sonoff_trvzb_native"}
    assert [e.file for e in result.errors] == ["keys.yaml"]
    assert "source_select" in str(result.errors[0])


def test_load_profile_file_raises_load_error_with_file_name() -> None:
    with pytest.raises(loader.ProfileLoadError) as exc:
        loader.load_profile_file(Path("tests/profiles/invalid/bad_encoder.yaml"))
    assert exc.value.file == "bad_encoder.yaml"
    assert str(exc.value)
    with pytest.raises(loader.ProfileLoadError) as missing:
        loader.load_profile_file(Path("tests/profiles/invalid/absent.yaml"))
    assert missing.value.file == "absent.yaml"
