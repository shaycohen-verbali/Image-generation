import React, { useState } from 'react'
import { buildApiUrl, createExport, getExport } from '../lib/api'

export default function ExportsPage() {
  const [statusFilter, setStatusFilter] = useState('')
  const [lastExportId, setLastExportId] = useState('')
  const [exportDetail, setExportDetail] = useState(null)
  const [message, setMessage] = useState('')

  const create = async () => {
    setMessage('Creating export...')
    try {
      const payload = {}
      if (statusFilter) payload.status = [statusFilter]
      const result = await createExport(payload)
      setLastExportId(result.id)
      setExportDetail(result)
      setMessage(`Created export ${result.id}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const load = async () => {
    if (!lastExportId) return
    setMessage('Loading export...')
    try {
      const result = await getExport(lastExportId)
      setExportDetail(result)
      setMessage(`Loaded export ${result.id}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  return (
    <section className="card-grid">
      <article className="card">
        <h2>Export Bundle</h2>
        <label>
          Run status filter (optional)
          <input value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} placeholder="completed_pass" />
        </label>
        <div className="inline-fields">
          <button onClick={create}>Create Export</button>
          <button onClick={load} disabled={!lastExportId}>Refresh Last Export</button>
        </div>

        {exportDetail && (
          <>
            <h3>Export {exportDetail.id}</h3>
            <p>Status: <strong>{exportDetail.status}</strong></p>

            <div className="inline-fields">
              {exportDetail.csv_path ? (
                <a href={buildApiUrl(exportDetail.csv_download_url)} target="_blank" rel="noreferrer">Download CSV</a>
              ) : (
                <span>CSV not ready</span>
              )}
              {exportDetail.zip_path ? (
                <a href={buildApiUrl(exportDetail.white_bg_zip_download_url)} target="_blank" rel="noreferrer">
                  Download ZIP (No Background)
                </a>
              ) : (
                <span>No-background ZIP not ready</span>
              )}
              {exportDetail.with_bg_zip_path ? (
                <a href={buildApiUrl(exportDetail.with_bg_zip_download_url)} target="_blank" rel="noreferrer">
                  Download ZIP (With Background)
                </a>
              ) : (
                <span>With-background ZIP not ready</span>
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
        )}
      </article>

      <article className="card message-card">
        <h2>Status</h2>
        <p>{message}</p>
      </article>
    </section>
  )
}
