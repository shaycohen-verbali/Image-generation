import React, { useEffect, useMemo, useState } from 'react'
import { buildApiUrl, createExport, exportCsvJob, listCsvJobs, listRuns } from '../lib/api'

const LEGACY_STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'queued', label: 'Queued' },
  { value: 'retry_queued', label: 'Retry queued' },
  { value: 'running', label: 'Running' },
  { value: 'cancel_requested', label: 'Stopping' },
  { value: 'completed_pass', label: 'Completed pass' },
  { value: 'completed_fail_threshold', label: 'Completed below threshold' },
  { value: 'failed_technical', label: 'Technical failure' },
  { value: 'canceled', label: 'Canceled' },
]

const LEGACY_EXPORT_FIELD_OPTIONS = [
  { key: 'word', label: 'Word' },
  { key: 'part_of_sentence', label: 'Part of sentence' },
  { key: 'category', label: 'Category' },
  { key: 'synonyms', label: 'Synonyms' },
  { key: 'base_asset_slug', label: 'Base asset slug' },
  { key: 'context', label: 'Context' },
  { key: 'need_a_person', label: 'Need a person' },
  { key: 'prompt_1', label: 'Prompt 1' },
  { key: 'file_name_1', label: 'File name 1' },
  { key: 'image_1', label: 'Image 1 path' },
  { key: 'prompt_2', label: 'Prompt 2' },
  { key: 'file_name_2', label: 'File name 2' },
  { key: 'image_2', label: 'Image 2 path' },
  { key: 'upgraded_prompt', label: 'Upgraded prompt' },
  { key: 'file_name_upgraded', label: 'Upgraded file name' },
  { key: 'upgraded_image_2', label: 'Upgraded image path' },
  { key: 'file_name_without_background', label: 'White background file name' },
  { key: 'image_without_background', label: 'White background image path' },
  { key: 'boy_or_girl', label: 'Boy or girl' },
]

function formatLocalDateTime(value) {
  if (!value) return '-'
  const raw = String(value).trim()
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(raw) ? raw : `${raw}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

function prettyCsvJobStatus(value) {
  const raw = String(value || '').trim().toLowerCase()
  if (!raw) return '-'
  if (raw === 'partial_failed') return 'partially failed'
  return raw.replaceAll('_', ' ')
}

function exportSourceSummary(item) {
  const filter = item?.filter_json || {}
  const runIds = Array.isArray(filter.run_ids) ? filter.run_ids.filter(Boolean) : []
  const entryIds = Array.isArray(filter.entry_ids) ? filter.entry_ids.filter(Boolean) : []
  if (runIds.length === 1) return `Run ${runIds[0]}`
  if (runIds.length > 1) return `${runIds.length} runs`
  if (entryIds.length === 1) return `Entry ${entryIds[0]}`
  if (entryIds.length > 1) return `${entryIds.length} entries`
  const statuses = Array.isArray(filter.status) ? filter.status.filter(Boolean) : []
  if (statuses.length === 1) return `Status ${statuses[0]}`
  return 'Legacy export'
}

function triggerDownload(path) {
  const url = buildApiUrl(path)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.rel = 'noreferrer'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

export default function ExportsPage() {
  const [sourceMode, setSourceMode] = useState('csv_job')
  const [statusFilter, setStatusFilter] = useState('')
  const [selectedRunId, setSelectedRunId] = useState('')
  const [selectedCsvJobId, setSelectedCsvJobId] = useState('')
  const [runs, setRuns] = useState([])
  const [csvJobs, setCsvJobs] = useState([])
  const [preparedExport, setPreparedExport] = useState(null)
  const [message, setMessage] = useState('')
  const [selectedExportFields, setSelectedExportFields] = useState(() => LEGACY_EXPORT_FIELD_OPTIONS.map((item) => item.key))

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) || null,
    [runs, selectedRunId]
  )
  const selectedCsvJob = useMemo(
    () => csvJobs.find((job) => job.id === selectedCsvJobId) || null,
    [csvJobs, selectedCsvJobId]
  )

  const refreshData = async () => {
    const [runsResult, csvJobsResult] = await Promise.allSettled([listRuns(), listCsvJobs()])

    if (runsResult.status === 'fulfilled') {
      const runsData = runsResult.value
      setRuns(runsData)
      if (!selectedRunId && runsData.length) {
        setSelectedRunId(runsData[0].id)
      }
    } else {
      setRuns([])
    }

    if (csvJobsResult.status === 'fulfilled') {
      const csvJobsData = csvJobsResult.value
      setCsvJobs(csvJobsData)
      if (!selectedCsvJobId && csvJobsData.length) {
        setSelectedCsvJobId(csvJobsData[0].id)
      }
    } else {
      setCsvJobs([])
    }

    const failures = [runsResult, csvJobsResult]
      .filter((result) => result.status === 'rejected')
      .map((result) => result.reason?.message || 'Failed to fetch')
    if (failures.length) {
      setMessage(`Error: ${failures.join(' | ')}`)
    }
  }

  useEffect(() => {
    refreshData()
  }, [])

  useEffect(() => {
    if (sourceMode === 'legacy_run' && !runs.length && csvJobs.length) {
      setSourceMode('csv_job')
    }
    if (sourceMode === 'csv_job' && !csvJobs.length && runs.length) {
      setSourceMode('legacy_run')
    }
  }, [sourceMode, runs.length, csvJobs.length])

  const create = async () => {
    setMessage(sourceMode === 'csv_job' ? 'Preparing CSV DAG package...' : 'Preparing legacy export package...')
    try {
      if (sourceMode === 'csv_job') {
        if (!selectedCsvJobId) {
          setMessage('Select a CSV job first')
          return
        }
        const result = await exportCsvJob(selectedCsvJobId)
        const nextPrepared = {
          kind: 'csv_job',
          id: result.job_id,
          batch_id: result.batch_id,
          file_name: result.file_name,
          package_download_url: result.download_url,
        }
        setPreparedExport(nextPrepared)
        setMessage(`Prepared CSV job package for ${result.job_id}`)
        triggerDownload(result.download_url)
        return
      }

      const payload = {}
      if (statusFilter) payload.status = [statusFilter]
      if (selectedRunId) payload.run_ids = [selectedRunId]
      payload.export_fields = selectedExportFields
      if (!selectedExportFields.length) {
        setMessage('Choose at least one export field')
        return
      }
      const result = await createExport(payload)
      const nextPrepared = {
        ...result,
        kind: 'legacy',
      }
      setPreparedExport(nextPrepared)
      setMessage(`Prepared export ${result.id}`)
      if (result.package_zip_download_url) {
        triggerDownload(result.package_zip_download_url)
      }
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const toggleExportField = (fieldKey) => {
    setSelectedExportFields((current) =>
      current.includes(fieldKey) ? current.filter((value) => value !== fieldKey) : [...current, fieldKey]
    )
  }

  return (
    <section className="card-grid">
      <article className="card">
        <h2>Create New Export</h2>
        <p className="config-help-text">
          Choose the source you want, confirm the specific run or CSV job number below, and then download the package directly.
        </p>

        <div className="form-grid">
          <label>
            Source
            <select value={sourceMode} onChange={(e) => setSourceMode(e.target.value)}>
              <option value="csv_job">CSV DAG job package</option>
              <option value="legacy_run">Legacy run bundle</option>
            </select>
          </label>

          {sourceMode === 'csv_job' ? (
            <>
              <label>
                Pick a CSV job
                <select value={selectedCsvJobId} onChange={(e) => setSelectedCsvJobId(e.target.value)}>
                  <option value="">Select a CSV job</option>
                  {csvJobs.map((job) => (
                    <option key={job.id} value={job.id}>
                      {`${job.batch_id} · ${prettyCsvJobStatus(job.status)} · ${formatLocalDateTime(job.created_at)}`}
                    </option>
                  ))}
                </select>
              </label>
              {selectedCsvJob ? (
                <div className="form-grid">
                  <p className="config-help-text"><strong>CSV job number:</strong> <span style={{ wordBreak: 'break-all' }}>{selectedCsvJob.id}</span></p>
                  <p className="config-help-text"><strong>Batch number:</strong> <span style={{ wordBreak: 'break-all' }}>{selectedCsvJob.batch_id}</span></p>
                  <p className="config-help-text"><strong>Status:</strong> {prettyCsvJobStatus(selectedCsvJob.status)}</p>
                  <p className="config-help-text"><strong>Created:</strong> {formatLocalDateTime(selectedCsvJob.created_at)}</p>
                </div>
              ) : (
                <p className="config-help-text">Choose a CSV job to download its package zip directly.</p>
              )}
            </>
          ) : (
            <>
              <label>
                Pick a run
                <select value={selectedRunId} onChange={(e) => setSelectedRunId(e.target.value)}>
                  <option value="">Select a run</option>
                  {runs.map((run) => (
                    <option key={run.id} value={run.id}>
                      {`${run.word || 'word'} · ${run.part_of_sentence || 'pos'} · ${run.category || 'category'} · ${formatLocalDateTime(run.created_at)}`}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Legacy run status filter
                <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                  {LEGACY_STATUS_OPTIONS.map((option) => (
                    <option key={option.value || 'all'} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              {selectedRun ? (
                <div className="form-grid">
                  <p className="config-help-text"><strong>Run number:</strong> <span style={{ wordBreak: 'break-all' }}>{selectedRun.id}</span></p>
                  <p className="config-help-text"><strong>Word:</strong> {selectedRun.word}</p>
                  <p className="config-help-text"><strong>POS:</strong> {selectedRun.part_of_sentence}</p>
                  <p className="config-help-text"><strong>Category:</strong> {selectedRun.category || '-'}</p>
                  <p className="config-help-text"><strong>Created:</strong> {formatLocalDateTime(selectedRun.created_at)}</p>
                </div>
              ) : (
                <p className="config-help-text">Choose a legacy run to export. The status filter is optional.</p>
              )}
              <div>
                <p className="config-help-text">
                  Choose which database-backed fields to include in the exported CSV. Images and manifest downloads stay available separately.
                </p>
                <div className="inline-fields">
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={() => setSelectedExportFields(LEGACY_EXPORT_FIELD_OPTIONS.map((item) => item.key))}
                  >
                    Select All
                  </button>
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={() => setSelectedExportFields([])}
                  >
                    Clear All
                  </button>
                  <span className="config-help-text">{selectedExportFields.length} fields selected</span>
                </div>
                <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
                  {LEGACY_EXPORT_FIELD_OPTIONS.map((field) => (
                    <label key={field.key} style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                      <input
                        type="checkbox"
                        checked={selectedExportFields.includes(field.key)}
                        onChange={() => toggleExportField(field.key)}
                      />
                      <span>{field.label}</span>
                    </label>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>

        <div className="inline-fields">
          <button onClick={create}>
            {sourceMode === 'csv_job' ? 'Download CSV Job Package' : 'Create And Download Export'}
          </button>
          <button onClick={refreshData} className="button-secondary">Refresh Lists</button>
        </div>
      </article>

      <article className="card message-card">
        <h2>Status</h2>
        <p>{message || 'No export has been prepared yet.'}</p>

        {preparedExport ? (
          <div className="form-grid">
            {preparedExport.kind === 'csv_job' ? (
              <>
                <p className="config-help-text"><strong>CSV job number:</strong> <span style={{ wordBreak: 'break-all' }}>{preparedExport.id}</span></p>
                <p className="config-help-text"><strong>Batch number:</strong> <span style={{ wordBreak: 'break-all' }}>{preparedExport.batch_id}</span></p>
                <p className="config-help-text"><strong>File name:</strong> {preparedExport.file_name}</p>
                <div className="inline-fields">
                  <button type="button" onClick={() => triggerDownload(preparedExport.package_download_url)}>
                    Download Package Again
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="config-help-text"><strong>Export number:</strong> <span style={{ wordBreak: 'break-all' }}>{preparedExport.id}</span></p>
                <p className="config-help-text"><strong>Source:</strong> {exportSourceSummary(preparedExport)}</p>
                <p className="config-help-text"><strong>Status:</strong> {preparedExport.status}</p>
                <p className="config-help-text">
                  <strong>Selected fields:</strong>{' '}
                  {Array.isArray(preparedExport.filter_json?.export_fields) && preparedExport.filter_json.export_fields.length
                    ? preparedExport.filter_json.export_fields.join(', ')
                    : 'All default fields'}
                </p>
                <div className="inline-fields">
                  {preparedExport.csv_download_url ? (
                    <button type="button" onClick={() => triggerDownload(preparedExport.csv_download_url)}>Download CSV</button>
                  ) : null}
                  {preparedExport.white_bg_zip_download_url ? (
                    <button type="button" onClick={() => triggerDownload(preparedExport.white_bg_zip_download_url)}>Download White Background ZIP</button>
                  ) : null}
                  {preparedExport.with_bg_zip_download_url ? (
                    <button type="button" onClick={() => triggerDownload(preparedExport.with_bg_zip_download_url)}>Download With Background ZIP</button>
                  ) : null}
                  {preparedExport.package_zip_download_url ? (
                    <button type="button" onClick={() => triggerDownload(preparedExport.package_zip_download_url)}>Download Full Package</button>
                  ) : null}
                  {preparedExport.manifest_download_url ? (
                    <button type="button" onClick={() => triggerDownload(preparedExport.manifest_download_url)}>Download Manifest</button>
                  ) : null}
                </div>
              </>
            )}
          </div>
        ) : null}
      </article>
    </section>
  )
}
