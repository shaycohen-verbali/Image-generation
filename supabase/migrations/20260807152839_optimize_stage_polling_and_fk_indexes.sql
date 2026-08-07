-- Persist compact cost entries so polling does not read large stage payloads.
ALTER TABLE public.stage_results
    ADD COLUMN IF NOT EXISTS cost_summary_json text;

-- Foreign-key columns used by CSV asset/task reconciliation and joins.
CREATE INDEX IF NOT EXISTS ix_csv_job_items_base_regular_asset_id
    ON public.csv_job_items (base_regular_asset_id);

CREATE INDEX IF NOT EXISTS ix_csv_job_items_base_white_bg_asset_id
    ON public.csv_job_items (base_white_bg_asset_id);

CREATE INDEX IF NOT EXISTS ix_csv_task_nodes_source_asset_id
    ON public.csv_task_nodes (source_asset_id);

CREATE INDEX IF NOT EXISTS ix_csv_task_nodes_regular_asset_id
    ON public.csv_task_nodes (regular_asset_id);

CREATE INDEX IF NOT EXISTS ix_csv_task_nodes_white_bg_asset_id
    ON public.csv_task_nodes (white_bg_asset_id);
