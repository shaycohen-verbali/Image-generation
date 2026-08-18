create table if not exists public.csv_job_summary_tasks (
    csv_job_id varchar(64) primary key
        references public.csv_jobs(id) on delete cascade,
    status varchar(16) not null default 'pending',
    target_pricing_version varchar(64) not null default '',
    attempt_count integer not null default 0,
    max_attempts integer not null default 3,
    worker_id varchar(160),
    last_error text not null default '',
    available_at timestamp without time zone not null default timezone('utc', now()),
    started_at timestamp without time zone,
    finished_at timestamp without time zone,
    created_at timestamp without time zone not null default timezone('utc', now()),
    updated_at timestamp without time zone not null default timezone('utc', now()),
    constraint ck_csv_job_summary_tasks_status
        check (status in ('pending', 'running', 'ready', 'failed')),
    constraint ck_csv_job_summary_tasks_attempts
        check (attempt_count >= 0 and max_attempts >= 1)
);

create index if not exists ix_csv_job_summary_tasks_pending
    on public.csv_job_summary_tasks (available_at, created_at)
    where status = 'pending';

create index if not exists ix_csv_job_summary_tasks_running_started
    on public.csv_job_summary_tasks (started_at)
    where status = 'running';
