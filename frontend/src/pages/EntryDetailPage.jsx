import React, { useState } from 'react'
import { getRun } from '../lib/api'

export default function EntryDetailPage() {
  const [runId, setRunId] = useState('')
  const [data, setData] = useState(null)
  const [message, setMessage] = useState('')

  const load = async () => {
    if (!runId) return
    setMessage('Loading run...')
    try {
      const detail = await getRun(runId)
      setData(detail)
      setMessage(`Loaded run ${runId}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  return (
    <section className="card-grid">
      <article className="card">
        <h2>Run Details</h2>
        <div className="inline-fields">
          <label>
            Run ID
            <input value={runId} onChange={(e) => setRunId(e.target.value)} placeholder="run_xxx" />
          </label>
          <button onClick={load}>Load</button>
        </div>

        {data && (
          <>
            <h3>Run</h3>
            <pre>{JSON.stringify(data.run, null, 2)}</pre>

            <h3>Prompts</h3>
            <pre>{JSON.stringify(data.prompts, null, 2)}</pre>

            <h3>Assets</h3>
            <pre>{JSON.stringify(data.assets, null, 2)}</pre>

            <h3>Scores</h3>
            <pre>{JSON.stringify(data.scores, null, 2)}</pre>
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
