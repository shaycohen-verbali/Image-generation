-- Keep the sense-image index aligned with the current active image slots in
-- word_inventory. Recreated images can have new storage paths, so storage path
-- alone cannot be the identity of the current image for a sense/profile slot.
CREATE OR REPLACE FUNCTION public.aac_sync_sense_images_from_inventory(
  requested_image_style text DEFAULT 'aac_current',
  requested_style_version text DEFAULT '1',
  requested_storage_bucket text DEFAULT 'aac-images-v1'
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  current_slot record;
  synced_count integer := 0;
BEGIN
  -- First reconcile every slot that exists on an active inventory row. Blank
  -- slots remove their old sense-image rows; populated slots remove old paths
  -- for the same sense/profile/background before upserting the current path.
  FOR current_slot IN
    SELECT
      inventory.sense_id,
      inventory.canonical_word,
      inventory.part_of_speech,
      NULLIF(BTRIM(field.storage_path), '') AS storage_path,
      CASE
        WHEN BTRIM(field.storage_path) LIKE 'supabase://%/%' THEN split_part(
          substr(BTRIM(field.storage_path), length('supabase://') + 1),
          '/',
          1
        )
        ELSE requested_storage_bucket
      END AS storage_bucket,
      CASE
        WHEN BTRIM(field.storage_path) LIKE 'supabase://%/%' THEN regexp_replace(
          BTRIM(field.storage_path),
          '^supabase://[^/]+/',
          ''
        )
        ELSE NULLIF(BTRIM(field.storage_path), '')
      END AS normalized_storage_path,
      split_part(field.slot_name, '_', 1) AS age_group,
      split_part(field.slot_name, '_', 2) AS gender,
      split_part(field.slot_name, '_', 3) AS skin_tone,
      CASE
        WHEN field.slot_name LIKE '%_white_bg_path' THEN 'white_bg'
        ELSE 'regular'
      END AS background_style,
      field.slot_name AS source_slot
    FROM public.word_inventory AS inventory
    CROSS JOIN LATERAL jsonb_each_text(to_jsonb(inventory)) AS field(slot_name, storage_path)
    WHERE inventory.sense_id IS NOT NULL
      AND inventory.is_active
      AND field.slot_name ~ '^(toddler|kid|tween|teenager)_(male|female)_(white|black|asian|brown)_(regular|white_bg)_path$'
  LOOP
    DELETE FROM public.aac_sense_images AS existing
    WHERE existing.image_style = requested_image_style
      AND existing.style_version = requested_style_version
      AND existing.sense_id = current_slot.sense_id
      AND existing.storage_bucket = current_slot.storage_bucket
      AND existing.subject_age_group = current_slot.age_group
      AND existing.subject_gender = current_slot.gender
      AND existing.subject_skin_tone = current_slot.skin_tone
      AND existing.background_style = current_slot.background_style
      AND (
        current_slot.normalized_storage_path IS NULL
        OR existing.storage_path <> current_slot.normalized_storage_path
      );

    IF current_slot.normalized_storage_path IS NULL THEN
      CONTINUE;
    END IF;

    INSERT INTO public.aac_sense_images (
      sense_id,
      storage_bucket,
      storage_path,
      alt_text,
      image_style,
      style_version,
      subject_skin_tone,
      subject_gender,
      subject_age_group,
      background_style,
      selection_criteria,
      generation_metadata
    ) VALUES (
      current_slot.sense_id,
      current_slot.storage_bucket,
      current_slot.normalized_storage_path,
      COALESCE(current_slot.canonical_word, current_slot.sense_id),
      requested_image_style,
      requested_style_version,
      current_slot.skin_tone,
      current_slot.gender,
      current_slot.age_group,
      current_slot.background_style,
      jsonb_build_object(
        'skin_tone', current_slot.skin_tone,
        'gender', current_slot.gender,
        'age_group', current_slot.age_group,
        'background', current_slot.background_style,
        'source_slot', current_slot.source_slot
      ),
      jsonb_build_object(
        'source', 'word_inventory',
        'source_slot', current_slot.source_slot,
        'part_of_speech', current_slot.part_of_speech
      )
    )
    ON CONFLICT (storage_bucket, storage_path) DO UPDATE
      SET sense_id = EXCLUDED.sense_id,
          alt_text = EXCLUDED.alt_text,
          image_style = EXCLUDED.image_style,
          style_version = EXCLUDED.style_version,
          subject_skin_tone = EXCLUDED.subject_skin_tone,
          subject_gender = EXCLUDED.subject_gender,
          subject_age_group = EXCLUDED.subject_age_group,
          background_style = EXCLUDED.background_style,
          selection_criteria = EXCLUDED.selection_criteria,
          generation_metadata = COALESCE(public.aac_sense_images.generation_metadata, '{}'::jsonb)
            || EXCLUDED.generation_metadata,
          updated_at = NOW();

    synced_count := synced_count + 1;
  END LOOP;

  -- Remove rows for inventory records or slots that were deleted/deactivated,
  -- as well as any old path that no longer appears in the active inventory.
  DELETE FROM public.aac_sense_images AS existing
  WHERE existing.image_style = requested_image_style
    AND existing.style_version = requested_style_version
    AND NOT EXISTS (
      SELECT 1
      FROM public.word_inventory AS inventory
      CROSS JOIN LATERAL jsonb_each_text(to_jsonb(inventory)) AS field(slot_name, storage_path)
      WHERE inventory.sense_id = existing.sense_id
        AND inventory.is_active
        AND field.slot_name ~ '^(toddler|kid|tween|teenager)_(male|female)_(white|black|asian|brown)_(regular|white_bg)_path$'
        AND NULLIF(BTRIM(field.storage_path), '') IS NOT NULL
        AND (
          CASE
            WHEN BTRIM(field.storage_path) LIKE 'supabase://%/%' THEN split_part(
              substr(BTRIM(field.storage_path), length('supabase://') + 1),
              '/',
              1
            )
            ELSE requested_storage_bucket
          END
        ) = existing.storage_bucket
        AND (
          CASE
            WHEN BTRIM(field.storage_path) LIKE 'supabase://%/%' THEN regexp_replace(
              BTRIM(field.storage_path),
              '^supabase://[^/]+/',
              ''
            )
            ELSE NULLIF(BTRIM(field.storage_path), '')
          END
        ) = existing.storage_path
        AND split_part(field.slot_name, '_', 1) = existing.subject_age_group
        AND split_part(field.slot_name, '_', 2) = existing.subject_gender
        AND split_part(field.slot_name, '_', 3) = existing.subject_skin_tone
        AND (
          CASE
            WHEN field.slot_name LIKE '%_white_bg_path' THEN 'white_bg'
            ELSE 'regular'
          END
        ) = existing.background_style
    );

  RETURN synced_count;
END;
$function$;
