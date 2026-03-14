import React, { useEffect, useState } from 'react'
import {
  applyEntryProfileOptions,
  createEntry,
  createRuns,
  getConfig,
  importCsv,
  importCsvJob,
  startCsvJob,
  updateConfig,
} from '../lib/api'

export default function SubmitPage() {
  const IMAGE_ASPECT_RATIO_OPTIONS = ['1:1', '2:3', '3:2', '3:4', '4:3', '9:16', '16:9', '21:9']
  const IMAGE_RESOLUTION_OPTIONS = ['1K', '2K', '4K']
  const IMAGE_FORMAT_OPTIONS = [
    { value: 'image/jpeg', label: 'JPEG (.jpg)' },
    { value: 'image/png', label: 'PNG (.png)' },
    { value: 'image/webp', label: 'WEBP (.webp)' },
  ]
  const NANO_BANANA_SAFETY_OPTIONS = [
    { value: 'default', label: 'Provider default' },
    { value: 'off', label: 'Off' },
    { value: 'block_none', label: 'Block none' },
    { value: 'block_only_high', label: 'Block only high' },
    { value: 'block_medium_and_above', label: 'Block medium and above' },
    { value: 'block_low_and_above', label: 'Block low and above' },
  ]
  const SAMPLE_CSV_URL = `${import.meta.env.BASE_URL || '/'}test_word_list.csv`
  const SAMPLE_CSV_NAME = 'test_word_list.csv'
  const defaultGender = 'male'
  const defaultAge = 'kid'
  const defaultSkinColor = 'white'
  const [form, setForm] = useState({
    word: '',
    part_of_sentence: '',
    category: '',
    context: '',
    person_gender_options: [defaultGender],
    person_age_options: [defaultAge],
    person_skin_color_options: [defaultSkinColor],
    batch: '',
  })
  const [lastEntryId, setLastEntryId] = useState('')
  const [message, setMessage] = useState('')
  const [uploadResult, setUploadResult] = useState(null)
  const [csvExecutionMode, setCsvExecutionMode] = useState('legacy')
  const [runWorkerCount, setRunWorkerCount] = useState(1)
  const [variantWorkerCount, setVariantWorkerCount] = useState(2)
  const [promptEngineerMode, setPromptEngineerMode] = useState('responses_api')
  const [promptEngineerModel, setPromptEngineerModel] = useState('gpt-5.4')
  const [stage3CritiqueModel, setStage3CritiqueModel] = useState('gpt-5.4')
  const [stage3GenerateModel, setStage3GenerateModel] = useState('nano-banana-2')
  const [qualityGateModel, setQualityGateModel] = useState('gpt-4o-mini')
  const [imageAspectRatio, setImageAspectRatio] = useState('1:1')
  const [imageResolution, setImageResolution] = useState('1K')
  const [imageFormat, setImageFormat] = useState('image/jpeg')
  const [nanoBananaSafetyLevel, setNanoBananaSafetyLevel] = useState('default')

  const toggleOption = (field, option, { locked = false } = {}) => {
    if (locked) return
    setForm((current) => {
      const currentValues = Array.isArray(current[field]) ? current[field] : []
      const nextValues = currentValues.includes(option)
        ? currentValues.filter((value) => value !== option)
        : [...currentValues, option]
      return {
        ...current,
        [field]: nextValues,
      }
    })
  }

  const selectedGenderCount = form.person_gender_options.length
  const selectedAgeCount = form.person_age_options.length
  const selectedSkinCount = form.person_skin_color_options.length
  const selectedCombinationCount = selectedGenderCount * selectedAgeCount * selectedSkinCount
  const generatedProfileCap = 16
  const generatedProfileCount = Math.min(selectedCombinationCount, generatedProfileCap)
  const extraVariantCount = Math.max(0, generatedProfileCount - 1)

  useEffect(() => {
    let mounted = true
    const loadConfig = async () => {
      try {
        const config = await getConfig()
        if (mounted && config?.max_parallel_runs) {
          setRunWorkerCount(config.max_parallel_runs)
        }
        if (mounted && config?.max_variant_workers) {
          setVariantWorkerCount(config.max_variant_workers)
        }
        if (mounted && config?.prompt_engineer_mode) {
          setPromptEngineerMode(config.prompt_engineer_mode)
        }
        if (mounted && config?.responses_prompt_engineer_model) {
          setPromptEngineerModel(config.responses_prompt_engineer_model)
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
        if (mounted && config?.image_aspect_ratio) {
          setImageAspectRatio(config.image_aspect_ratio)
        }
        if (mounted && config?.image_resolution) {
          setImageResolution(config.image_resolution)
        }
        if (mounted && config?.image_format) {
          setImageFormat(config.image_format)
        }
        if (mounted && config?.nano_banana_safety_level) {
          setNanoBananaSafetyLevel(config.nano_banana_safety_level)
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
    if (!form.word.trim() || !form.part_of_sentence.trim()) {
      setMessage('Enter the word and part of sentence first')
      return
    }
    setMessage('Saving current entry and queueing run...')
    try {
      const entry = await createEntry(form)
      setLastEntryId(entry.id)
      const runs = await createRuns({ entry_ids: [entry.id] })
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
      if (csvExecutionMode === 'csv_dag') {
        const result = await importCsvJob(file, {
          execution_mode: 'csv_dag',
          person_gender_options: form.person_gender_options,
          person_age_options: form.person_age_options,
          person_skin_color_options: form.person_skin_color_options,
        })
        setUploadResult({ ...result, mode: 'csv_dag' })
        setMessage(`Imported ${result.imported_count} rows into DAG job ${result.batch_id}`)
      } else {
        const result = await importCsv(file)
        setUploadResult({ ...result, mode: 'legacy' })
        setMessage(
          result.batch_id
            ? `Imported ${result.imported_count} rows into job ${result.batch_id}`
            : `Imported ${result.imported_count} rows`
        )
      }
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onUseSampleCsv = async () => {
    setMessage('Loading sample CSV...')
    try {
      const response = await fetch(SAMPLE_CSV_URL)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const blob = await response.blob()
      const file = new File([blob], SAMPLE_CSV_NAME, { type: 'text/csv' })
      if (csvExecutionMode === 'csv_dag') {
        const result = await importCsvJob(file, {
          execution_mode: 'csv_dag',
          person_gender_options: form.person_gender_options,
          person_age_options: form.person_age_options,
          person_skin_color_options: form.person_skin_color_options,
        })
        setUploadResult({ ...result, mode: 'csv_dag' })
        setMessage(`Imported sample CSV into DAG job ${result.batch_id}`)
      } else {
        const result = await importCsv(file)
        setUploadResult({ ...result, mode: 'legacy' })
        setMessage(
          result.batch_id
            ? `Imported sample CSV into job ${result.batch_id}`
            : 'Imported sample CSV'
        )
      }
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onQueueImported = async () => {
    if (!uploadResult) return
    if (uploadResult.mode === 'csv_dag') {
      setMessage('Starting CSV DAG job...')
      try {
        const result = await startCsvJob(uploadResult.job_id)
        setUploadResult((current) => (current ? { ...current, status: result.status } : current))
        setMessage(`Started CSV DAG job ${result.job_id}`)
      } catch (error) {
        setMessage(`Error: ${error.message}`)
      }
      return
    }
    const entryIds = uploadResult.rows.filter((r) => r.entry_id).map((r) => r.entry_id)
    if (!entryIds.length) {
      setMessage('No valid rows to queue')
      return
    }
    setMessage('Applying current person variants and queueing imported entries...')
    try {
      await applyEntryProfileOptions({
        entry_ids: entryIds,
        person_gender_options: form.person_gender_options,
        person_age_options: form.person_age_options,
        person_skin_color_options: form.person_skin_color_options,
      })
      const runs = await createRuns({ entry_ids: entryIds })
      setMessage(`Queued ${runs.length} runs with the current person variant settings`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onSaveWorkerConfig = async () => {
    const parsedRuns = Number(runWorkerCount)
    const parsedVariants = Number(variantWorkerCount)
    if (!Number.isInteger(parsedRuns) || parsedRuns < 1 || parsedRuns > 12) {
      setMessage('Run workers must be an integer between 1 and 12')
      return
    }
    if (!Number.isInteger(parsedVariants) || parsedVariants < 1 || parsedVariants > 12) {
      setMessage('Variant workers must be an integer between 1 and 12')
      return
    }
    setMessage('Saving worker configuration...')
    try {
      const updated = await updateConfig({ max_parallel_runs: parsedRuns, max_variant_workers: parsedVariants })
      setRunWorkerCount(updated.max_parallel_runs)
      setVariantWorkerCount(updated.max_variant_workers)
      setMessage(`Saved workers: run=${updated.max_parallel_runs}, variants=${updated.max_variant_workers}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const saveModelConfig = async (updates, successMessage = 'Saved model configuration') => {
    try {
      const updated = await updateConfig(updates)
      if (updated.stage3_critique_model) setStage3CritiqueModel(updated.stage3_critique_model)
      if (updated.stage3_generate_model) setStage3GenerateModel(updated.stage3_generate_model)
      if (updated.quality_gate_model) setQualityGateModel(updated.quality_gate_model)
      setMessage(successMessage)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const savePromptEngineerConfig = async (updates, successMessage) => {
    try {
      const updated = await updateConfig(updates)
      if (updated.prompt_engineer_mode) setPromptEngineerMode(updated.prompt_engineer_mode)
      if (updated.responses_prompt_engineer_model) setPromptEngineerModel(updated.responses_prompt_engineer_model)
      setMessage(successMessage)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const saveImageOutputConfig = async (updates, successMessage) => {
    try {
      const updated = await updateConfig(updates)
      if (updated.image_aspect_ratio) setImageAspectRatio(updated.image_aspect_ratio)
      if (updated.image_resolution) setImageResolution(updated.image_resolution)
      if (updated.image_format) setImageFormat(updated.image_format)
      if (updated.nano_banana_safety_level) setNanoBananaSafetyLevel(updated.nano_banana_safety_level)
      setMessage(successMessage)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  return (
    <section className="submit-page">
      <article className="card submit-settings-card">
        <h2>Shared Run Settings</h2>
        <p>Set these once before you run. The same person variants and output settings apply to both Single Concept and Bulk CSV jobs.</p>
        <div className="submit-settings-grid">
          <div className="option-group-card">
            <h3>Person Variants</h3>
            <p className="config-help-text">
              The base run always uses `male`, `kid (5-9)`, and `White`. Any extra checked options create additional final-image variants and white-background variants only when the concept requires a person.
            </p>
            <div className="form-grid">
              <div>
                <strong>Applies To Single Concept And Bulk CSV</strong>
              </div>
              <fieldset className="checkbox-group">
                <legend>Gender</legend>
                <label className="checkbox-option checkbox-option-locked">
                  <input type="checkbox" checked readOnly disabled />
                  <span>Male (default base run)</span>
                </label>
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={form.person_gender_options.includes('female')}
                    onChange={() => toggleOption('person_gender_options', 'female')}
                  />
                  <span>Female</span>
                </label>
              </fieldset>
              <fieldset className="checkbox-group">
                <legend>Age</legend>
                <label className="checkbox-option checkbox-option-locked">
                  <input type="checkbox" checked readOnly disabled />
                  <span>Kid (5-9) (default base run)</span>
                </label>
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={form.person_age_options.includes('toddler')}
                    onChange={() => toggleOption('person_age_options', 'toddler')}
                  />
                  <span>Toddler (2-4)</span>
                </label>
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={form.person_age_options.includes('tween')}
                    onChange={() => toggleOption('person_age_options', 'tween')}
                  />
                  <span>Tween (10-14)</span>
                </label>
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={form.person_age_options.includes('teenager')}
                    onChange={() => toggleOption('person_age_options', 'teenager')}
                  />
                  <span>Teenager (15-18)</span>
                </label>
              </fieldset>
              <fieldset className="checkbox-group">
                <legend>Skin color</legend>
                <label className="checkbox-option checkbox-option-locked">
                  <input type="checkbox" checked readOnly disabled />
                  <span>White (default base run)</span>
                </label>
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={form.person_skin_color_options.includes('black')}
                    onChange={() => toggleOption('person_skin_color_options', 'black')}
                  />
                  <span>Black</span>
                </label>
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={form.person_skin_color_options.includes('asian')}
                    onChange={() => toggleOption('person_skin_color_options', 'asian')}
                  />
                  <span>Asian</span>
                </label>
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={form.person_skin_color_options.includes('brown')}
                    onChange={() => toggleOption('person_skin_color_options', 'brown')}
                  />
                  <span>Brown (Indian origin)</span>
                </label>
              </fieldset>
              <p className="config-help-text">
                Selected combinations: {selectedCombinationCount} total person profile{selectedCombinationCount === 1 ? '' : 's'}.
                {selectedCombinationCount > generatedProfileCap
                  ? ` The generator will use a capped review set of ${generatedProfileCount} profiles to avoid creating too many images.`
                  : ''}
                {extraVariantCount > 0
                  ? ` This means ${extraVariantCount} additional final-image variant${extraVariantCount === 1 ? '' : 's'} plus ${extraVariantCount} additional white-background variant${extraVariantCount === 1 ? '' : 's'}.`
                  : ' No extra person variants will be created beyond the base run.'}
              </p>
            </div>
          </div>
          <div className="option-group-card">
            <h3>Image Output</h3>
            <p className="config-help-text">Single Concept and Bulk CSV jobs both use the same aspect ratio, resolution, format, and Nano Banana safety level.</p>
            <div className="submit-output-grid">
              <label>
                Output aspect ratio
                <select
                  value={imageAspectRatio}
                  onChange={(e) => {
                    const value = e.target.value
                    setImageAspectRatio(value)
                    setMessage('Saving output aspect ratio...')
                    saveImageOutputConfig(
                      { image_aspect_ratio: value },
                      `Saved output aspect ratio: ${value}`
                    )
                  }}
                >
                  {IMAGE_ASPECT_RATIO_OPTIONS.map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              </label>
              <label>
                Output resolution
                <select
                  value={imageResolution}
                  onChange={(e) => {
                    const value = e.target.value
                    setImageResolution(value)
                    setMessage('Saving output resolution...')
                    saveImageOutputConfig(
                      { image_resolution: value },
                      `Saved output resolution: ${value}`
                    )
                  }}
                >
                  {IMAGE_RESOLUTION_OPTIONS.map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              </label>
              <label>
                Output image format
                <select
                  value={imageFormat}
                  onChange={(e) => {
                    const value = e.target.value
                    setImageFormat(value)
                    setMessage('Saving output image format...')
                    saveImageOutputConfig(
                      { image_format: value },
                      `Saved output image format: ${value}`
                    )
                  }}
                >
                  {IMAGE_FORMAT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label>
                Nano Banana safety
                <select
                  value={nanoBananaSafetyLevel}
                  onChange={(e) => {
                    const value = e.target.value
                    setNanoBananaSafetyLevel(value)
                    setMessage('Saving Nano Banana safety level...')
                    saveImageOutputConfig(
                      { nano_banana_safety_level: value },
                      `Saved Nano Banana safety level: ${value}`
                    )
                  }}
                >
                  {NANO_BANANA_SAFETY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              <p className="config-help-text submit-output-note">
                Aspect ratio defaults to `1:1`. Resolution defaults to `1K`. Format defaults to `JPEG`. Safety level maps to Gemini `safetySettings` thresholds for Nano Banana requests.
              </p>
            </div>
          </div>
        </div>
      </article>

      <article className="card message-card submit-status-card">
        <h2>Status</h2>
        <p>{message}</p>
      </article>

      <div className="submit-actions-grid">
        <article className="card">
          <h2>Single Concept</h2>
          <p>Enter one concept here. This run will use the shared Person Variants and Image Output settings from above.</p>
          <form className="form-grid submit-form-grid" onSubmit={onSubmit}>
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
              Batch
              <input value={form.batch} onChange={(e) => setForm({ ...form, batch: e.target.value })} />
            </label>
          </form>
          <div className="inline-fields submit-action-row">
            <button type="button" onClick={onSubmit}>Save Entry</button>
            <button onClick={onRun}>Start Run For Last Entry</button>
          </div>
        </article>

        <article className="card">
          <h2>Bulk CSV Import</h2>
          <p>Import rows here, then either queue legacy runs or start the new dependency-based CSV DAG job using the same shared settings from above.</p>
          <div className="form-grid">
            <label>
              CSV execution mode
              <select value={csvExecutionMode} onChange={(e) => setCsvExecutionMode(e.target.value)}>
                <option value="legacy">Legacy fallback runs</option>
                <option value="csv_dag">Parallel CSV DAG</option>
              </select>
            </label>
            <label>
              CSV file
              <input type="file" accept=".csv" onChange={onCsvUpload} />
            </label>
          </div>
          <div className="inline-fields">
            <button type="button" onClick={onUseSampleCsv}>Use Sample CSV</button>
            <a href={SAMPLE_CSV_URL} download={SAMPLE_CSV_NAME}>Download sample CSV</a>
          </div>
          <button onClick={onQueueImported} disabled={!uploadResult}>
            {uploadResult?.mode === 'csv_dag' ? 'Start CSV DAG Job' : 'Queue Runs For Imported Rows'}
          </button>
          {uploadResult && (
            <div>
              <p>Imported: {uploadResult.imported_count}, Skipped: {uploadResult.skipped_count}</p>
              {uploadResult.batch_id ? <p>Batch id: {uploadResult.batch_id}</p> : null}
              {uploadResult.job_id ? <p>CSV DAG job: {uploadResult.job_id}</p> : null}
              {uploadResult.status ? <p>Status: {uploadResult.status}</p> : null}
            </div>
          )}
        </article>
      </div>

      <div className="submit-support-grid">
        <article className="card">
          <h2>Processing Speed</h2>
          <p>Split throughput by goal: run workers start whole words, variant workers fan out image variations inside a run.</p>
          <div className="inline-fields">
            <label>
              Run workers
              <input
                type="number"
                min="1"
                max="12"
                value={runWorkerCount}
                onChange={(e) => setRunWorkerCount(e.target.value)}
              />
            </label>
            <label>
              Variant workers
              <input
                type="number"
                min="1"
                max="12"
                value={variantWorkerCount}
                onChange={(e) => setVariantWorkerCount(e.target.value)}
              />
            </label>
            <button type="button" onClick={onSaveWorkerConfig}>Save Workers</button>
          </div>
          <p className="config-help-text">
            Recommendation for the current 512 MB Render instance is still <strong>1</strong> run worker and <strong>2</strong> variant workers. Higher values are now allowed, but memory use and provider pressure will rise quickly.
          </p>
        </article>

        <article className="card">
          <h2>Model Selection</h2>
          <p>Choose models for Stage 3 critique, Stage 3 upgraded image, and Quality Gate scoring. Changes are saved automatically.</p>
          <div className="form-grid submit-compact-form">
            <label>
              Stage 3.1 Vision Critique
              <select
                value={stage3CritiqueModel}
                onChange={(e) => {
                  const value = e.target.value
                  setStage3CritiqueModel(value)
                  setMessage('Saving Stage 3 critique model...')
                  saveModelConfig({ stage3_critique_model: value }, `Saved Stage 3 critique model: ${value}`)
                }}
              >
                <option value="gpt-4o-mini">gpt-4o-mini</option>
                <option value="gpt-5.4">gpt-5.4</option>
                <option value="gemini-3-flash">Gemini-3-flash</option>
                <option value="gemini-3-pro">Gemini-3-pro</option>
              </select>
            </label>
            <label>
              Stage 3.3 Upgraded Image
              <select
                value={stage3GenerateModel}
                onChange={(e) => {
                  const value = e.target.value
                  setStage3GenerateModel(value)
                  setMessage('Saving Stage 3 upgraded image model...')
                  saveModelConfig({ stage3_generate_model: value }, `Saved Stage 3 upgraded image model: ${value}`)
                }}
              >
                <option value="flux-1.1-pro">Flux 1.1 Pro</option>
                <option value="imagen-3">Imagen 3</option>
                <option value="imagen-4">Imagen 4</option>
                <option value="nano-banana">Nano Banana</option>
                <option value="nano-banana-2">Nano Banana 2</option>
                <option value="nano-banana-pro">Nano Banana Pro</option>
              </select>
            </label>
            <label>
              Quality Gate
              <select
                value={qualityGateModel}
                onChange={(e) => {
                  const value = e.target.value
                  setQualityGateModel(value)
                  setMessage('Saving Quality Gate model...')
                  saveModelConfig({ quality_gate_model: value }, `Saved Quality Gate model: ${value}`)
                }}
              >
                <option value="gpt-4o-mini">gpt-4o-mini</option>
                <option value="gemini-3-flash">Gemini-3-flash</option>
                <option value="gemini-3-pro">Gemini-3-pro</option>
              </select>
            </label>
          </div>
        </article>

        <article className="card">
          <h2>Prompt Engineer</h2>
          <p>Choose which prompt engineer to use when you start new runs. Detailed prompt-engineer settings live in Runs + Details.</p>
          <div className="form-grid submit-compact-form">
            <label>
              Prompt engineer mode
              <select
                value={promptEngineerMode}
                onChange={(e) => {
                  const value = e.target.value
                  setPromptEngineerMode(value)
                  setMessage('Saving prompt engineer mode...')
                  savePromptEngineerConfig(
                    { prompt_engineer_mode: value },
                    `Saved prompt engineer mode: ${value}`
                  )
                }}
              >
                <option value="responses_api">Option 2: Responses API / Direct Model</option>
                <option value="assistant">Option 1: OpenAI Assistant</option>
              </select>
            </label>
            <label>
              Prompt engineer model
              <select
                value={promptEngineerModel}
                onChange={(e) => {
                  const value = e.target.value
                  setPromptEngineerModel(value)
                  setMessage('Saving prompt engineer model...')
                  savePromptEngineerConfig(
                    { responses_prompt_engineer_model: value },
                    `Saved prompt engineer model: ${value}`
                  )
                }}
              >
                <option value="gpt-4o-mini">gpt-4o-mini</option>
                <option value="gpt-4.1-mini">gpt-4.1-mini</option>
                <option value="gpt-5.4">gpt-5.4</option>
                <option value="gemini-3-flash">Gemini-3-flash</option>
                <option value="gemini-3-pro">Gemini-3-pro</option>
              </select>
            </label>
            <p className="config-help-text">
              The selected mode and prompt engineer model are applied automatically when you click Start Run or Queue Runs.
            </p>
          </div>
        </article>
      </div>

    </section>
  )
}
