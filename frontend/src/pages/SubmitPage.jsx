import React, { useEffect, useState } from 'react'
import { applyEntryProfileOptions, createEntry, createRuns, getConfig, importCsv, updateConfig } from '../lib/api'

export default function SubmitPage() {
  const IMAGE_ASPECT_RATIO_OPTIONS = ['1:1', '2:3', '3:2', '3:4', '4:3', '9:16', '16:9', '21:9']
  const IMAGE_RESOLUTION_OPTIONS = ['1K', '2K', '4K']
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
  const [runWorkerCount, setRunWorkerCount] = useState(1)
  const [variantWorkerCount, setVariantWorkerCount] = useState(2)
  const [promptEngineerMode, setPromptEngineerMode] = useState('responses_api')
  const [promptEngineerModel, setPromptEngineerModel] = useState('gpt-5.4')
  const [stage3CritiqueModel, setStage3CritiqueModel] = useState('gpt-5.4')
  const [stage3GenerateModel, setStage3GenerateModel] = useState('nano-banana-2')
  const [qualityGateModel, setQualityGateModel] = useState('gpt-4o-mini')
  const [imageAspectRatio, setImageAspectRatio] = useState('1:1')
  const [imageResolution, setImageResolution] = useState('1K')

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
      const result = await importCsv(file)
      setUploadResult(result)
      setMessage(
        result.batch_id
          ? `Imported ${result.imported_count} rows into job ${result.batch_id}`
          : `Imported ${result.imported_count} rows`
      )
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
      const result = await importCsv(file)
      setUploadResult(result)
      setMessage(
        result.batch_id
          ? `Imported sample CSV into job ${result.batch_id}`
          : 'Imported sample CSV'
      )
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
    if (!Number.isInteger(parsedRuns) || parsedRuns < 1 || parsedRuns > 4) {
      setMessage('Run workers must be an integer between 1 and 4')
      return
    }
    if (!Number.isInteger(parsedVariants) || parsedVariants < 1 || parsedVariants > 8) {
      setMessage('Variant workers must be an integer between 1 and 8')
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
      setMessage(successMessage)
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
          <div className="form-grid option-group-card">
            <div>
              <strong>Person Variants</strong>
              <p className="config-help-text">
                The base run always uses `male`, `kid (5-9)`, and `White`. Any extra checked options create additional final-image variants and white-background variants only when the concept requires a person.
              </p>
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
        <div className="inline-fields">
          <button type="button" onClick={onUseSampleCsv}>Use Sample CSV</button>
          <a href={SAMPLE_CSV_URL} download={SAMPLE_CSV_NAME}>Download sample CSV</a>
        </div>
        <button onClick={onQueueImported} disabled={!uploadResult}>Queue Runs For Imported Rows</button>
        {uploadResult && (
          <div>
            <p>Imported: {uploadResult.imported_count}, Skipped: {uploadResult.skipped_count}</p>
            {uploadResult.batch_id ? <p>Job id: {uploadResult.batch_id}</p> : null}
          </div>
        )}
      </article>

      <article className="card message-card">
        <h2>Status</h2>
        <p>{message}</p>
      </article>

      <article className="card">
        <h2>Processing Speed</h2>
        <p>Split throughput by goal: run workers start whole words, variant workers fan out image variations inside a run.</p>
        <div className="inline-fields">
          <label>
            Run workers
            <input
              type="number"
              min="1"
              max="4"
              value={runWorkerCount}
              onChange={(e) => setRunWorkerCount(e.target.value)}
            />
          </label>
          <label>
            Variant workers
            <input
              type="number"
              min="1"
              max="8"
              value={variantWorkerCount}
              onChange={(e) => setVariantWorkerCount(e.target.value)}
            />
          </label>
          <button type="button" onClick={onSaveWorkerConfig}>Save Workers</button>
        </div>
        <p className="config-help-text">
          Recommendation for the current 512 MB Render instance: use <strong>1</strong> run worker and <strong>2</strong> variant workers. If you upgrade memory, move to <strong>2</strong> run workers and <strong>4</strong> variant workers.
        </p>
      </article>

      <article className="card">
        <h2>Model Selection</h2>
        <p>Choose models for Stage 3 critique, Stage 3 upgraded image, and Quality Gate scoring. Changes are saved automatically.</p>
        <div className="form-grid">
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
        <div className="form-grid">
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

      <article className="card">
        <h2>Image Output</h2>
        <p>Set the output size before you run a word or queue a CSV job. These saved values apply to new image generation runs.</p>
        <div className="form-grid">
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
          <p className="config-help-text">
            Aspect ratio defaults to `1:1`. Resolution defaults to `1K`. These options follow the documented API settings used by the image-generation pipeline.
          </p>
        </div>
      </article>
    </section>
  )
}
