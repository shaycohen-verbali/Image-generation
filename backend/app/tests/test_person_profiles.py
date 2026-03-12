from app.models import Entry
from app.services.person_profiles import (
    additional_variant_profiles,
    all_selected_profiles,
    entry_default_profile,
    planned_review_profiles,
    profile_prompt_fragment,
    variant_branch_plan,
)


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
    assert len(planned_review_profiles(make_entry())) == 10
    assert len(additional_variant_profiles(make_entry())) == 9
    assert {"gender": "female", "age": "kid", "skin_color": "white"} in planned_review_profiles(make_entry())
    assert {"gender": "male", "age": "teenager", "skin_color": "white"} in planned_review_profiles(make_entry())


def test_profile_prompt_fragment_makes_age_and_gender_explicit() -> None:
    fragment = profile_prompt_fragment({"gender": "female", "age": "teenager", "skin_color": "brown"})
    assert "teenage girl" in fragment
    assert "15 to 18 years old" in fragment
    assert "visibly female" in fragment
    assert "Brown skin" in fragment
    assert "body size" in fragment or "full-body proportions" in fragment


def test_variant_branch_plan_creates_female_seed_then_female_variants() -> None:
    plan = variant_branch_plan(make_entry())
    assert plan["base_profile"] == {"gender": "male", "age": "kid", "skin_color": "white"}
    assert plan["female_seed"] == {"gender": "female", "age": "kid", "skin_color": "white"}
    assert all(profile["gender"] == "male" for profile in plan["male_variants"])
    assert all(profile["gender"] == "female" for profile in plan["female_variants"])
    assert {"gender": "female", "age": "kid", "skin_color": "black"} in plan["female_variants"]
