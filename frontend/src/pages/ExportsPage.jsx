import React, { useEffect, useMemo, useState } from 'react'
import { buildApiUrl, createExport, exportCsvJob, getExport, listCsvJobs, listExports, listRuns } from '../lib/api'

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

function formatLocalDateTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

export default function ExportsPage() {
  const [sourceMode, setSourceMode] = useState('legacy_run')
  const [statusFilter, setStatusFilter] = useState('')
  const [selectedRunId, setSelectedRunId] = useState('')
  const [selectedCsvJobId, setSelectedCsvJobId] = useState('')
  const [runs, setRuns] = useState([])
  const [csvJobs, setCsvJobs] = useState([])
  const [exportsList, setExportsList] = useState([])
  const [selectedExportId, setSelectedExportId] = useState('')
  const [exportDetail, setExportDetail] = useState(null)
  const [message, setMessage] = useState('')

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) || null,
    [runs, selectedRunId]
  )
  const selectedCsvJob = useMemo(
    () => csvJobs.find((job) => job.id === selectedCsvJobId) || null,
    [csvJobs, selectedCsvJobId]
  )

  const refreshData = async () => {
    try {
      const [runsData, csvJobsData, exportsData] = await Promise.all([listRuns(), listCsvJobs(), listExports()])
      setRuns(runsData)
      setCsvJobs(csvJobsData)
      setExportsList(exportsData)
      if (!selectedRunId && runsData.length) {
        setSelectedRunId(runsData[0].id)
      }
      if (!selectedCsvJobId && csvJobsData.length) {
        setSelectedCsvJobId(csvJobsData[0].id)
      }
      if (!selectedExportId && exportsData.length) {
        setSelectedExportId(exportsData[0].id)
        setExportDetail(exportsData[0])
      }
    } catch (error) {
      setMessage(`Error: ${error.message}`)
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

  useEffect(() => {
    if (!selectedExportId) return
    const match = exportsList.find((item) => item.id === selectedExportId)
    if (match) {
      setExportDetail(match)
    }
  }, [exportsList, selectedExportId])

  const create = async () => {
    setMessage('Creating export...')
    try {
      if (sourceMode === 'csv_job') {
        if (!selectedCsvJobId) {
          setMessage('Select a CSV job first')
          return
        }
        const result = await exportCsvJob(selectedCsvJobId)
        window.open(buildApiUrl(result.download_url), '_blank', 'noopener,noreferrer')
        setMessage(`Prepared CSV job package for ${selectedCsvJob?.batch_id || selectedCsvJobId}`)
      } else {
        const payload = {}
        if (statusFilter) payload.status = [statusFilter]
        if (selectedRunId) payload.run_ids = [selectedRunId]
        const result = await createExport(payload)
        setSelectedExportId(result.id)
        setExportDetail(result)
        setMessage(`Created export ${result.id}`)
        refreshData()
      }
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const load = async () => {
    if (!selectedExportId) return
    setMessage('Loading export...')
    try {
      const result = await getExport(selectedExportId)
      setExportDetail(result)
      setMessage(`Loaded export ${result.id}`)
      refreshData()
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  return (
    <section className="card-grid">
      <article className="card">
        <h2>Create New Export</h2>
        <p className="config-help-text">
          Export Builder creates a new downloadable package. Export History lets you reopen packages that were already created earlier.
        </p>
        <div className="form-grid">
          <label>
            Source
            <select value={sourceMode} onChange={(e) => setSourceMode(e.target.value)}>
              <option value="legacy_run">Legacy run bundle</option>
              <option value="csv_job">CSV DAG job package</option>
            </select>
          </label>
          {sourceMode === 'csv_job' ? (
            <label>
              Pick a CSV job
              <select value={selectedCsvJobId} onChange={(e) => setSelectedCsvJobId(e.target.value)}>
                <option value="">Select a CSV job</option>
                {csvJobs.map((job) => (
                  <option key={job.id} value={job.id}>
                    {`${job.batch_id} · ${job.status} · ${formatLocalDateTime(job.created_at)}`}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <>
              <label>
                Pick a run (optional)
                <select value={selectedRunId} onChange={(e) => setSelectedRunId(e.target.value)}>
                  <option value="">All legacy runs</option>
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
              <p className="config-help-text">
                This filter only narrows legacy run exports. Leave it on All statuses unless you want to export one specific legacy run state.
              </p>
            </>
          )}
        </div>

        {sourceMode === 'csv_job' ? (
          selectedCsvJob ? (
            <p className="config-help-text">
              Selected CSV job: {selectedCsvJob.batch_id} · {selectedCsvJob.status} · {formatLocalDateTime(selectedCsvJob.created_at)}
            </p>
          ) : (
            <p className="config-help-text">Choose a CSV job to download its package zip directly.</p>
          )
        ) : selectedRun ? (
          <p className="config-help-text">
            Selected run: {selectedRun.word} · {selectedRun.part_of_sentence} · {selectedRun.category || 'no category'} · {formatLocalDateTime(selectedRun.created_at)}
          </p>
        ) : runs.length === 0 ? (
          <p className="config-help-text">No legacy runs are available right now. Switch to CSV DAG job package if that is the flow you want to export.</p>
        ) : (
          <p className="config-help-text">No specific run selected. Export will include all legacy runs matching the optional filter.</p>
        )}

        <div className="inline-fields">
          <button onClick={create}>{sourceMode === 'csv_job' ? 'Download CSV Job Package' : 'Create Export'}</button>
          <button onClick={refreshData} className="button-secondary">Refresh Lists</button>
        </div>
      </article>

      <article className="card">
        <h2>Export History</h2>
        <p className="config-help-text">
          Pick a previously created export here if you want to download it again without rebuilding it.
        </p>
        <label>
          Choose export
          <select value={selectedExportId} onChange={(e) => setSelectedExportId(e.target.value)}>
            <option value="">Select an export</option>
            {exportsList.map((item) => (
              <option key={item.id} value={item.id}>
                {`${item.id} · ${item.status} · ${formatLocalDateTime(item.created_at)}`}
              </option>
            ))}
          </select>
        </label>
        <div className="inline-fields">
          <button onClick={load} disabled={!selectedExportId}>Load Export</button>
        </div>

        {exportDetail ? (
          <>
            <h3>Export {exportDetail.id}</h3>
            <p>Status: <strong>{exportDetail.status}</strong></p>
            <p>Created: {formatLocalDateTime(exportDetail.created_at)}</p>

            <div className="inline-fields">
              {exportDetail.csv_path ? (
                <a href={buildApiUrl(exportDetail.csv_download_url)} target="_blank" rel="noreferrer">Download CSV</a>
              ) : (
                <span>CSV not ready</span>
              )}
              {exportDetail.zip_path ? (
                <a href={buildApiUrl(exportDetail.white_bg_zip_download_url)} target="_blank" rel="noreferrer">
                  Download ZIP (White Background)
                </a>
              ) : (
                <span>White-background ZIP not ready</span>
              )}
              {exportDetail.with_bg_zip_path ? (
                <a href={buildApiUrl(exportDetail.with_bg_zip_download_url)} target="_blank" rel="noreferrer">
                  Download ZIP (With Background)
                </a>
              ) : (
                <span>With-background ZIP not ready</span>
              )}
              {exportDetail.package_zip_path ? (
                <a href={buildApiUrl(exportDetail.package_zip_download_url)} target="_blank" rel="noreferrer">
                  Download Full Package
                </a>
              ) : (
                <span>Package ZIP not ready</span>
              )}
              {exportDetail.manifest_path ? (
                <a href={buildApiUrl(exportDetail.manifest_download_url)} target="_blank" rel="noreferrer">
                  Download Manifest
                </a>
              ) : (
                <span>Manifest not ready</span>
              )}
            </div>

            <pre>{JSON.stringify(exportDetail, null, 2)}</pre>
          </>
        ) : (
          <p>Select an export to inspect it.</p>
        )}
      </article>

      <article className="card message-card">
        <h2>Status</h2>
        <p>{message}</p>
      </article>
    </section>
  )
}
