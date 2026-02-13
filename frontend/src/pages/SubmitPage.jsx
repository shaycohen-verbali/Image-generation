import React, { useEffect, useState } from 'react'
import { createEntry, createRuns, getConfig, importCsv, updateConfig } from '../lib/api'

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
  const [workerCount, setWorkerCount] = useState(10)
  const [stage3CritiqueModel, setStage3CritiqueModel] = useState('gpt-4o-mini')
  const [stage3GenerateModel, setStage3GenerateModel] = useState('flux-1.1-pro')
  const [qualityGateModel, setQualityGateModel] = useState('gpt-4o-mini')

  useEffect(() => {
    let mounted = true
    const loadConfig = async () => {
      try {
        const config = await getConfig()
        if (mounted && config?.max_parallel_runs) {
          setWorkerCount(config.max_parallel_runs)
        }
        if (mounted && (config?.stage3_critique_model || config?.openai_model_vision)) {
          setStage3CritiqueModel(config.stage3_critique_model || config.openai_model_vision)
        }
        if (mounted && config?.stage3_generate_model) {
          setStage3GenerateModel(config.stage3_generate_model)
        }
        if (mounted && (config?.quality_gate_model || config?.openai_model_vision)) {
          setQualityGateModel(config.quality_gate_model || config.openai_model_vision)
        }
      } catch (_error) {
        // Keep default UI value when config endpoint is unavailable.
      }
    }
    loadConfig()
    return () => {
      mounted = false
    }
  }, [])

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

  const onSaveWorkerConfig = async () => {
    const parsed = Number(workerCount)
    if (!Number.isInteger(parsed) || parsed < 1 || parsed > 50) {
      setMessage('Workers must be an integer between 1 and 50')
      return
    }
    setMessage('Saving worker configuration...')
    try {
      const updated = await updateConfig({ max_parallel_runs: parsed })
      setWorkerCount(updated.max_parallel_runs)
      setMessage(`Saved workers: ${updated.max_parallel_runs}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onSaveModelConfig = async () => {
    setMessage('Saving model configuration...')
    try {
      const updated = await updateConfig({
        stage3_critique_model: stage3CritiqueModel,
        stage3_generate_model: stage3GenerateModel,
        quality_gate_model: qualityGateModel,
      })
      setStage3CritiqueModel(updated.stage3_critique_model)
      setStage3GenerateModel(updated.stage3_generate_model)
      setQualityGateModel(updated.quality_gate_model)
      setMessage('Saved model configuration')
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
            Category (optional)
            <input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} />
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

      <article className="card">
        <h2>Processing Speed</h2>
        <p>Set how many runs the worker can process in parallel.</p>
        <div className="inline-fields">
          <label>
            Max parallel workers
            <input
              type="number"
              min="1"
              max="50"
              value={workerCount}
              onChange={(e) => setWorkerCount(e.target.value)}
            />
          </label>
          <button type="button" onClick={onSaveWorkerConfig}>Save Workers</button>
        </div>
      </article>

      <article className="card">
        <h2>Model Selection</h2>
        <p>Choose models for Stage 3 critique, Stage 3 upgraded image, and Quality Gate scoring.</p>
        <div className="form-grid">
          <label>
            Stage 3.1 Vision Critique
            <select value={stage3CritiqueModel} onChange={(e) => setStage3CritiqueModel(e.target.value)}>
              <option value="gpt-4o-mini">gpt-4o-mini</option>
              <option value="gemini-3-flash">Gemini-3-flash</option>
              <option value="gemini-3-pro">Gemini-3-pro</option>
            </select>
          </label>
          <label>
            Stage 3.3 Upgraded Image
            <select value={stage3GenerateModel} onChange={(e) => setStage3GenerateModel(e.target.value)}>
              <option value="flux-1.1-pro">Flux 1.1 Pro</option>
              <option value="imagen-3">Imagen 3</option>
              <option value="imagen-4">Imagen 4</option>
              <option value="nano-banana">Nano Banana</option>
              <option value="nano-banana-pro">Nano Banana Pro</option>
            </select>
          </label>
          <label>
            Quality Gate
            <select value={qualityGateModel} onChange={(e) => setQualityGateModel(e.target.value)}>
              <option value="gpt-4o-mini">gpt-4o-mini</option>
              <option value="gemini-3-flash">Gemini-3-flash</option>
              <option value="gemini-3-pro">Gemini-3-pro</option>
            </select>
          </label>
          <button type="button" onClick={onSaveModelConfig}>Save Models</button>
        </div>
      </article>
    </section>
  )
}
