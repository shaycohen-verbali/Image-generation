from app.models import Entry
from app.services.person_profiles import (
    additional_variant_profiles,
    all_selected_profiles,
    entry_default_profile,
    planned_review_profiles,
    profile_edit_instruction,
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


def test_variant_branch_plan_follows_white_age_then_female_then_race_order() -> None:
    plan = variant_branch_plan(make_entry())
    assert plan["base_profile"] == {"gender": "male", "age": "kid", "skin_color": "white"}
    assert plan["female_seed"] == {"gender": "female", "age": "kid", "skin_color": "white"}
    assert all(profile["gender"] == "male" and profile["skin_color"] == "white" for profile in plan["male_age_variants"])
    assert all(profile["gender"] == "female" and profile["skin_color"] == "white" for profile in plan["female_age_variants"])
    assert {"gender": "female", "age": "kid", "skin_color": "black"} in plan["appearance_variants"]


def test_profile_edit_instruction_makes_age_gender_and_race_changes_explicit() -> None:
    age_instruction = profile_edit_instruction(
        {"gender": "male", "age": "teenager", "skin_color": "white"},
        {"gender": "male", "age": "kid", "skin_color": "white"},
    )
    assert "teenager (17 yo)" in age_instruction
    assert "body and head" in age_instruction
    assert "other objects near the human changes accordingly" in age_instruction

    gender_instruction = profile_edit_instruction(
        {"gender": "female", "age": "teenager", "skin_color": "white"},
        {"gender": "male", "age": "kid", "skin_color": "white"},
    )
    assert "female teenager (17 yo)" in gender_instruction

    race_instruction = profile_edit_instruction(
        {"gender": "female", "age": "teenager", "skin_color": "asian"},
        {"gender": "female", "age": "teenager", "skin_color": "white"},
    )
    assert "Asian-origin" in race_instruction
    assert "not any stigma features" in race_instruction
