-- Additive metadata that links each generated attempt to its exact prompt,
-- source image, and (only when selected) stable canonical winner path.
ALTER TABLE public.assets
  ADD COLUMN IF NOT EXISTS generation_prompt_id text REFERENCES public.prompts(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS source_asset_id text REFERENCES public.assets(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS canonical_path text;

CREATE INDEX IF NOT EXISTS ix_assets_generation_prompt_id
  ON public.assets(generation_prompt_id);
CREATE INDEX IF NOT EXISTS ix_assets_source_asset_id
  ON public.assets(source_asset_id);
CREATE INDEX IF NOT EXISTS ix_assets_canonical_path
  ON public.assets(canonical_path);

CREATE UNIQUE INDEX IF NOT EXISTS ux_assets_current_canonical_path
  ON public.assets(canonical_path)
  WHERE canonical_path IS NOT NULL;
