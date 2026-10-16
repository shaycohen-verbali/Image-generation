# AAC symbols profile

Select **AAC symbols** in Shared Run Settings → Additional profiles, or in a completed CSV job's continuation options. The demographic selections remain independent. Selecting more genders, ages or skin tones does not multiply the symbol output: there is one additional symbol profile per word.

## Source and generation

- The dependency is always `male:kid:white`, regardless of the other requested variants. Existing inventory sources are reused; a missing white-boy base is scheduled automatically.
- CSV DAG prefers that base's white-background delivery image, falling back to its regular image. Single Concept uses the winning white-boy base image.
- Nano Banana 2 receives the source image and `backend/app/assets/aac_symbols/style-reference-v1.png` together on every conversion. The fixed prompt includes the word, part of speech, sense/category and context, and handles objects, actions, expressions and interactions.
- Symbols use `1:1` output even when ordinary images use another aspect ratio. Resolution, saved format and safety settings retain the current run settings.
- A person-free word still receives a symbol. The demographic age/wardrobe critique is bypassed; semantic preservation checks the source's critical meaning against the symbol instead.
- In CSV DAG the generated symbol already has a white background, so both output slots are saved from the same generated pixels. No second restyling request is needed.

## Identity and storage

For compatibility with existing three-part task keys, `symbol` is a reserved output-profile option carried in `person_age_options`. It is not a demographic age and is expanded exactly once as `male:symbol:white`. The UI displays **AAC symbols**, separately from Age.

Inventory slots:

- `symbol_male_white_regular_path`
- `symbol_male_white_white_bg_path`
- `symbol_male_white_regular_prompt`
- `symbol_male_white_white_bg_prompt`

The existing additive startup schema upgrade creates these four columns on an existing inventory. Existing demographic paths are untouched. Inventory readback, export field selection, and package exports preserve the distinct symbol identity. MaTalk table exports identify these rows with `age=symbol`; consuming systems need to support that additional profile.

## Activation and validation

Restart the backend and worker with the updated source, and rebuild/reload the frontend when using a production build. Startup needs the same schema-alter privileges used by the app's existing inventory upgrades.

Automated checks cover independent profile expansion, the white-boy dependency, person-free generation, source and reference attachment, square requests, isolated inventory slots, idempotent inventory upgrades, and the Single Concept pipeline. Browser validation uses a separate local preview database and does not queue paid provider generation. Actual Nano Banana output quality remains to be validated with live generation.

Keep the reference and prompt versioned together; do not replace the reference with each newly generated image.
