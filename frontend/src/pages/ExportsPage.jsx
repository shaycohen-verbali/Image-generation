import React, { useEffect, useMemo, useState } from 'react'
import { buildApiUrl, createExport, getExport, listExports, listRuns } from '../lib/api'

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
  const [statusFilter, setStatusFilter] = useState('')
  const [selectedRunId, setSelectedRunId] = useState('')
  const [runs, setRuns] = useState([])
  const [exportsList, setExportsList] = useState([])
  const [selectedExportId, setSelectedExportId] = useState('')
  const [exportDetail, setExportDetail] = useState(null)
  const [message, setMessage] = useState('')

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) || null,
    [runs, selectedRunId]
  )

  const refreshData = async () => {
    try {
      const [runsData, exportsData] = await Promise.all([listRuns(), listExports()])
      setRuns(runsData)
      setExportsList(exportsData)
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
    if (!selectedExportId) return
    const match = exportsList.find((item) => item.id === selectedExportId)
    if (match) {
      setExportDetail(match)
    }
  }, [exportsList, selectedExportId])

  const create = async () => {
    setMessage('Creating export...')
    try {
      const payload = {}
      if (statusFilter) payload.status = [statusFilter]
      if (selectedRunId) payload.run_ids = [selectedRunId]
      const result = await createExport(payload)
      setSelectedExportId(result.id)
      setExportDetail(result)
      setMessage(`Created export ${result.id}`)
      refreshData()
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
        <h2>Export Builder</h2>
        <div className="form-grid">
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
            Run status filter (optional)
            <input value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} placeholder="completed_pass" />
          </label>
        </div>

        {selectedRun ? (
          <p className="config-help-text">
            Selected run: {selectedRun.word} · {selectedRun.part_of_sentence} · {selectedRun.category || 'no category'} · {formatLocalDateTime(selectedRun.created_at)}
          </p>
        ) : (
          <p className="config-help-text">No specific run selected. Export will include all legacy runs matching the optional filter.</p>
        )}

        <div className="inline-fields">
          <button onClick={create}>Create Export</button>
          <button onClick={refreshData} className="button-secondary">Refresh Lists</button>
        </div>
      </article>

      <article className="card">
        <h2>Export History</h2>
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
