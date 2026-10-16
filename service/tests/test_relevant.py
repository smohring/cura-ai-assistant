from cura_ai.relevant import RELEVANT_KEYS


def test_every_relevant_key_is_a_real_setting_listed_once(flsun):
    assert len(RELEVANT_KEYS) == len(set(RELEVANT_KEYS))
    assert [k for k in RELEVANT_KEYS if k not in flsun.specs] == []


def test_the_prompt_rows_cover_support_interface_and_surface_settings(flsun):
    keys = {row["key"] for row in flsun.describe(RELEVANT_KEYS)}
    assert {
        "support_roof_pattern",
        "speed_support_roof",
        "ironing_enabled",
        "acceleration_wall_0",
    } <= keys
