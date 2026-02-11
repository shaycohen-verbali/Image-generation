import React, { useState } from 'react'
import { createEntry, createRuns, importCsv } from '../lib/api'

export default function SubmitPage() {
  const [form, setForm] = useState({
    word: '',
    part_of_sentence: '',
    category: '',
    context: '',
    boy_or_girl: '',
    batch: '',
  })
  const [lastEntryId, setLastEntryId] = useState('')
  const [message, setMessage] = useState('')
  const [uploadResult, setUploadResult] = useState(null)

  const onSubmit = async (event) => {
    event.preventDefault()
    setMessage('Saving entry...')
    try {
      const entry = await createEntry(form)
      setLastEntryId(entry.id)
      setMessage(`Created entry ${entry.id}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onRun = async () => {
    if (!lastEntryId) {
      setMessage('Create an entry first')
      return
    }
    setMessage('Queueing run...')
    try {
      const runs = await createRuns({ entry_ids: [lastEntryId] })
      setMessage(`Queued run ${runs[0].id}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onCsvUpload = async (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    setMessage('Uploading CSV...')
    try {
      const result = await importCsv(file)
      setUploadResult(result)
      setMessage(`Imported ${result.imported_count} rows`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onQueueImported = async () => {
    if (!uploadResult) return
    const entryIds = uploadResult.rows.filter((r) => r.entry_id).map((r) => r.entry_id)
    if (!entryIds.length) {
      setMessage('No valid rows to queue')
      return
    }
    setMessage('Queueing imported entries...')
    try {
      const runs = await createRuns({ entry_ids: entryIds })
      setMessage(`Queued ${runs.length} runs`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  return (
    <section className="card-grid">
      <article className="card">
        <h2>Single Concept</h2>
        <form className="form-grid" onSubmit={onSubmit}>
          <label>
            Word
            <input value={form.word} onChange={(e) => setForm({ ...form, word: e.target.value })} required />
          </label>
          <label>
            Part of sentence
            <input value={form.part_of_sentence} onChange={(e) => setForm({ ...form, part_of_sentence: e.target.value })} required />
          </label>
          <label>
            Category
            <input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} required />
          </label>
          <label>
            Context
            <input value={form.context} onChange={(e) => setForm({ ...form, context: e.target.value })} />
          </label>
          <label>
            Boy or girl
            <input value={form.boy_or_girl} onChange={(e) => setForm({ ...form, boy_or_girl: e.target.value })} />
          </label>
          <label>
            Batch
            <input value={form.batch} onChange={(e) => setForm({ ...form, batch: e.target.value })} />
          </label>
          <button type="submit">Save Entry</button>
        </form>
        <button onClick={onRun}>Start Run For Last Entry</button>
      </article>

      <article className="card">
        <h2>Bulk CSV Import</h2>
        <input type="file" accept=".csv" onChange={onCsvUpload} />
        <button onClick={onQueueImported} disabled={!uploadResult}>Queue Runs For Imported Rows</button>
        {uploadResult && (
          <p>
            Imported: {uploadResult.imported_count}, Skipped: {uploadResult.skipped_count}
          </p>
        )}
      </article>

      <article className="card message-card">
        <h2>Status</h2>
        <p>{message}</p>
      </article>
    </section>
  )
}
