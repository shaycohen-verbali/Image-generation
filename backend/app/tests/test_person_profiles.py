from app.models import Entry
from app.services.person_profiles import additional_variant_profiles, all_selected_profiles, entry_default_profile, profile_prompt_fragment


def make_entry() -> Entry:
    return Entry(
        id="ent_profiles",
        word="carry",
        part_of_sentence="verb",
        category="",
        context="",
        boy_or_girl="male",
        person_gender_options_json='["male","female"]',
        person_age_options_json='["kid","toddler","tween","teenager"]',
        person_skin_color_options_json='["white","black","asian","brown"]',
        batch="",
        source_row_hash="hash",
    )


def test_default_profile_uses_locked_defaults_first() -> None:
    profile = entry_default_profile(make_entry())
    assert profile == {
        "gender": "male",
        "age": "kid",
        "skin_color": "white",
    }


def test_all_selected_profiles_returns_full_cross_product() -> None:
    profiles = all_selected_profiles(make_entry())
    assert len(profiles) == 32
    assert len(additional_variant_profiles(make_entry())) == 31


def test_profile_prompt_fragment_makes_age_and_gender_explicit() -> None:
    fragment = profile_prompt_fragment({"gender": "female", "age": "teenager", "skin_color": "brown"})
    assert "teenage girl" in fragment
    assert "15 to 18 years old" in fragment
    assert "visibly female" in fragment
    assert "Brown skin" in fragment
