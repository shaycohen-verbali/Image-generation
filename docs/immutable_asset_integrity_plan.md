# Selected Winner and Attempt Image Integrity Plan

## Purpose

Keep every generated attempt, select one winner, and ensure the database, Library, Runs + Details, variants, white-background images, and exports all use the same selected winner and its exact prompt.

This plan intentionally does **not** repair historical data. If an existing image is wrong, it will be recreated with the override command.

## Decisions

1. Attempt images are retained for now.
2. All attempts for a word/profile are stored in the same existing CSV job-item folder.
3. Each attempt has a unique filename and never overwrites another attempt.
4. The selected winner uses the existing canonical filename.
5. The white-background image is created from the selected regular winner.
6. An override replaces the old canonical regular and white-background images and updates the database to the new winner.
7. The old canonical images may be deleted after the replacement is verified. Attempt images are not deleted.
8. Variants continue to use the existing dependency graph and do not run through the full base pipeline.
9. Export names, CSV columns, ZIP folders, and `__v1` names remain unchanged.
10. No historical backfill, recovery search, or automatic historical repair is included.

## Storage layout and naming

The existing bucket and CSV job-item folder remain unchanged:

```text
supabase://images/csv-jobs/{job_id}/{item_id}/
```

### Attempt filenames

Every attempt uses the canonical stem plus an attempt number and a short unique identifier. The unique identifier prevents a later override from colliding with an older attempt that used the same attempt number.

```text
{canonical_stem}__attempt_{attempt_number:02d}__{short_asset_id}.jpg
```

Example:

```text
a__determiner__reg__m__kid__w__a281010be0a5c353__attempt_01__8f42c1.jpg
a__determiner__reg__m__kid__w__a281010be0a5c353__attempt_02__9bd872.jpg
a__determiner__reg__m__kid__w__a281010be0a5c353__attempt_03__a72f10.jpg
```

These files remain in Storage after winner selection and after future overrides.

### Winner filename

The selected winner uses the existing canonical filename with no `winner` label:

```text
a__determiner__reg__m__kid__w__a281010be0a5c353.jpg
```

The database—not the filename—identifies which attempt won. Winner promotion copies the selected attempt bytes to the canonical filename. It does not rename or delete the attempt file.

### White-background filename

The white-background image uses the existing canonical naming convention:

```text
a__determiner__wbg__m__kid__w__a281010be0a5c353.jpg
```

It must be generated from the exact selected regular winner, not from the last attempted image.

### Other profiles

Every variant uses the same rules with its own gender, age, and skin-tone components:

```text
a__determiner__reg__f__kid__w__a281010be0a5c353.jpg
a__determiner__wbg__f__kid__w__a281010be0a5c353.jpg
```

Variant attempts receive the same attempt and unique-ID suffix before promotion.

## Database model

### Attempt assets

Every generated attempt remains an `assets` row and records:

- Its unique attempt path.
- SHA-256 checksum.
- Run, stage, and attempt number.
- The exact generation prompt ID.
- The exact source asset ID when the image is derived from another image.

The prompt link is important because several variant profiles can share the same run, stage, and attempt number.

### Current canonical asset

Each selected profile/background has one current canonical asset record. It contains:

- Canonical Storage path.
- Checksum of the current canonical bytes.
- Exact winning prompt ID.
- Source attempt or source regular asset ID.
- Selected attempt number.

On override, this current record is updated to the newly selected winner. Historical attempt rows remain unchanged.

### Existing winner pointers

The existing pointers remain authoritative:

- `csv_job_items.base_regular_asset_id`
- `csv_job_items.base_white_bg_asset_id`
- `csv_task_nodes.regular_asset_id`
- `csv_task_nodes.white_bg_asset_id`

They must resolve to the current canonical records, not simply the latest attempt.

## Base generation workflow

For the white/male/kid base profile:

1. Generate each regular attempt with a unique attempt filename.
2. Store an asset row, checksum, and exact generation prompt for every attempt.
3. Score all attempts using the existing pipeline.
4. Select the winner using the existing scoring rules.
5. Copy the selected attempt to the canonical regular filename.
6. Verify the canonical regular bytes against the selected attempt checksum.
7. Generate the white-background image from that canonical regular winner.
8. Write and verify the canonical white-background image.
9. Update both current canonical asset records and winner pointers.
10. Update `word_inventory` paths and prompts from those same canonical asset records.
11. Run the existing `aac_sense_images` synchronization.
12. Keep every attempt image.

The pipeline must never choose a winner by looking at the last filename written.

## Variant workflow

The existing dependency graph remains unchanged. The white/male/kid image is the base; variants branch from the appropriate already-created parent image and do not rerun the full pipeline.

The current dependency behavior remains:

- White male kid → base pipeline.
- Non-white male kid → white male kid.
- White female kid → white male kid.
- Non-white female kid → white female kid.
- Non-kid profiles generally → the matching gender and skin-tone kid.
- White female teenager → white male teenager.

For each variant:

1. Read the selected canonical regular asset of its dependency.
2. Generate the variant attempt with a unique attempt filename.
3. Critique it using the existing variant logic.
4. If correction is required, save the correction as another unique attempt with its correction prompt.
5. Select the final regular variant attempt.
6. Copy it to that profile's canonical regular filename.
7. Generate the white-background variant from the selected canonical regular variant.
8. Update that task's regular and white-background pointers.
9. Synchronize inventory and sense-image rows.
10. Keep all original and corrected attempts.

A corrected variant's canonical record must reference the correction prompt, not the original generation prompt.

### Parent overrides

By default, overriding one profile replaces that profile's regular and white-background canonical images only. Existing descendant variants are not silently deleted or regenerated.

If descendants should be recreated from the new parent, they will be regenerated explicitly with their own override commands. A future cascade-override option can automate this, but it is not required for this implementation.

## Override workflow

An override follows the same generation flow without touching retained attempts:

1. Generate new uniquely named attempts.
2. Select and verify the new winner.
3. Create and verify its new white-background image in temporary/staging paths.
4. Replace the old canonical regular and white-background Storage objects.
5. Update the current canonical records with the new checksums, prompts, source assets, and attempt numbers.
6. Update the existing winner/task pointers if necessary.
7. Update the matching `word_inventory` paths and prompts together.
8. Synchronize `aac_sense_images`.
9. Delete superseded canonical objects only when they use different paths and are no longer referenced.
10. Do not delete any attempt images.

Because Supabase Storage and PostgreSQL do not share one transaction, new images must be fully generated and verified before changing the canonical records. If promotion fails, the old canonical records remain selected.

## Prompt integrity

The path and prompt for a selected image always come from the same canonical asset record:

```text
selected canonical asset
  → canonical path
  → checksum
  → exact generation_prompt_id
  → prompt text
```

Do not locate new variant or correction prompts using only run, stage, and attempt. Keep that lookup only as a legacy fallback.

## Library

No visible redesign is required.

- Continue reading selected paths and prompts from `word_inventory`.
- Show only canonical selected images, not attempt files.
- Version image tokens/cache keys with the selected checksum or `updated_at` value.
- After an override, the new checksum must produce a new cache version even though the canonical path is unchanged.

## Runs + Details

No visible redesign is required.

- Continue displaying all attempts.
- Clearly identify the selected attempt using the existing winner state.
- Serve each attempt using its unique attempt path.
- Serve the winner through the canonical asset pointer.
- Cache by asset ID and checksum rather than only by Storage URI.

## `word_inventory` and `aac_sense_images`

For every selected regular or white-background image:

1. Verify its current canonical checksum.
2. Update the matching `word_inventory` path and prompt together.
3. Invoke the existing `aac_sense_images` synchronization immediately after finalization or override.
4. Keep the existing job-finished and batch synchronization calls as a backup.

The canonical path may remain the same after an override, so consumers must treat the updated checksum or timestamp as the content version.

## Export behavior

The external export contract does not change.

- Preserve all existing CSV columns.
- Preserve ZIP structure.
- Preserve `images/regular` and `images/white_background` folders.
- Preserve current canonical `__v1` export filenames.
- Export only assets referenced by the current base/task winner pointers.
- Never include attempt files unless a future diagnostic export explicitly requests them.
- Verify the selected asset checksum before adding it to the ZIP.
- If Storage bytes do not match the selected checksum, stop or explicitly skip with a warning; never silently export the wrong image.
- The source filename inside Storage does not determine the exported filename.

Therefore, existing export consumers should see no change.

## Failure safety

Winner promotion must use this order:

```text
generate attempts
  → select winner
  → build white background from winner
  → verify both staged images
  → replace canonical objects
  → update canonical database metadata and pointers
  → update inventory
  → synchronize sense images
```

Additional rules:

- Do not remove or replace the selected canonical records before both new images are ready.
- Do not update the prompt separately from the image metadata.
- Do not package an export during a detected checksum mismatch.
- Attempt images remain available even if winner promotion fails.

## Scope exclusions

This implementation does not include:

- Repairing historical mutable images.
- Searching caches or backups for historical winners.
- Backfilling uncertain historical prompts.
- Deleting historical attempt images.
- Moving images into per-generation asset folders.
- Automatically regenerating every descendant after a parent override.
- Changing public API response shapes or export formats.

Existing bad data will be recreated through the override command.

## Implementation phases

### Phase 1 — Unique attempt names

- Change all base, correction, variant, and override attempt writes to unique filenames in the existing job-item folder.
- Retain all attempt files.
- Record the exact prompt and source asset on every new attempt.

### Phase 2 — Winner promotion

- Copy the selected attempt to the existing canonical regular filename.
- Generate white background from the selected winner.
- Verify both canonical checksums.
- Update the current canonical metadata and winner pointers.

### Phase 3 — Consumer consistency

- Update `word_inventory` path/prompt pairs from the current canonical records.
- Synchronize `aac_sense_images` immediately.
- Version Library caches by checksum or timestamp.
- Make Runs + Details distinguish attempts from the selected canonical image.

### Phase 4 — Export verification

- Preserve current export output.
- Select only current canonical assets through existing pointers.
- Verify checksums before packaging.

### Phase 5 — Override verification

- Recreate a representative base image through override.
- Confirm the old canonical image is replaced.
- Confirm old attempts remain.
- Confirm the new white-background image comes from the new regular winner.
- Confirm Library, Runs + Details, inventory, sense-image rows, and exports show the new selected bytes and prompt.

## Verification matrix

Automated and preview-environment testing must cover:

1. Multiple base attempts in one folder with unique filenames.
2. Selection of an earlier attempt after later attempts finish.
3. Winner promotion to the existing canonical regular filename.
4. White-background generation from the selected regular winner.
5. Retention of every attempt after selection.
6. Override producing new attempt filenames without collisions.
7. Override replacing the old canonical regular and white-background content.
8. Cache version change after an override using the same canonical path.
9. Variant generation from the correct dependency asset.
10. Variant correction becoming the selected canonical variant with the correction prompt.
11. Variant white background using the corrected regular variant.
12. `word_inventory` path and prompt coming from the same selected canonical record.
13. Immediate `aac_sense_images` synchronization.
14. Export containing only selected canonical images with current `__v1` names.
15. Export checksum mismatch being rejected or clearly reported.
16. Existing API and export formats remaining compatible.

## Branch and deployment sequence

1. Implement and test on `codex/immutable-asset-integrity`.
2. Deploy the frontend as a Vercel branch preview.
3. Deploy the backend from the feature branch on Render for preview testing.
4. Run the verification matrix without changing historical production images.
5. Test recreation through an explicit override command.
6. Merge only after approval.
7. Return Render and Vercel production configuration to `main` after merge.

## Completion criteria

The change is complete when:

- Attempts never overwrite one another and remain stored.
- The winner uses the current canonical filename.
- The canonical regular bytes match the selected attempt checksum.
- White background is created from the selected regular winner.
- Overrides replace canonical content without deleting attempts.
- Variants preserve their current dependency graph.
- Selected prompts match the images that produced them.
- Library, Runs + Details, inventory, and sense-image rows resolve the same current content.
- Exports remain externally unchanged and contain verified selected bytes.
