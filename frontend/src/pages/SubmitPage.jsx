import React, { useEffect, useState } from 'react'
import {
  API_BASE,
  applyEntryProfileOptions,
  createEntry,
  createRuns,
  getConfig,
  importCsv,
  importCsvJob,
  importWordSourceRows,
  listWordSourceRows,
  listWordSources,
  startCsvJob,
  updateConfig,
} from '../lib/api'

export default function SubmitPage() {
  const DEFAULT_PERSON_GENDER_OPTIONS = ['male']
  const DEFAULT_PERSON_AGE_OPTIONS = ['kid']
  const DEFAULT_PERSON_SKIN_COLOR_OPTIONS = ['white']
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
  const SAMPLE_CSV_URL = `${API_BASE}/csv-jobs/sample-csv`
  const SAMPLE_CSV_NAME = 'test_word_list.csv'
  const [form, setForm] = useState({
    word: '',
    part_of_sentence: '',
    category: '',
    context: '',
    person_gender_options: DEFAULT_PERSON_GENDER_OPTIONS,
    person_age_options: DEFAULT_PERSON_AGE_OPTIONS,
    person_skin_color_options: DEFAULT_PERSON_SKIN_COLOR_OPTIONS,
    batch: '',
  })
  const [lastEntryId, setLastEntryId] = useState('')
  const [message, setMessage] = useState('')
  const [uploadResult, setUploadResult] = useState(null)
  const [selectedCsvFile, setSelectedCsvFile] = useState(null)
  const [csvActivity, setCsvActivity] = useState({ active: false, label: '', hint: '' })
  const [csvActivityStartedAt, setCsvActivityStartedAt] = useState(null)
  const [csvActivityElapsedSeconds, setCsvActivityElapsedSeconds] = useState(0)
  const [csvExecutionMode, setCsvExecutionMode] = useState('csv_dag')
  const [wordSourceType, setWordSourceType] = useState('csv')
  const [wordSources, setWordSources] = useState([])
  const [selectedWordSource, setSelectedWordSource] = useState('word_inventory')
  const [wordSourceRows, setWordSourceRows] = useState([])
  const [selectedWordSourceRowIds, setSelectedWordSourceRowIds] = useState([])
  const [wordSourceSearch, setWordSourceSearch] = useState('')
  const [wordSourceTotal, setWordSourceTotal] = useState(0)
  const [wordSourceLoading, setWordSourceLoading] = useState(false)
  const [overrideExistingVariants, setOverrideExistingVariants] = useState(false)
  const [runWorkerCount, setRunWorkerCount] = useState(1)
  const [variantWorkerCount, setVariantWorkerCount] = useState(2)
  const [promptEngineerMode, setPromptEngineerMode] = useState('responses_api')
  const [promptEngineerModel, setPromptEngineerModel] = useState('gpt-5.4')
  const [stage3CritiqueModel, setStage3CritiqueModel] = useState('gpt-5.4')
  const [stage3AnatomyCritiqueModel, setStage3AnatomyCritiqueModel] = useState('gpt-5.4')
  const [stage3AccessibilityCritiqueModel, setStage3AccessibilityCritiqueModel] = useState('gpt-5.4')
  const [stage3GenerateModel, setStage3GenerateModel] = useState('gemini-3.1-flash-lite-image')
  const [postQualityAccessibilityCritiqueModel, setPostQualityAccessibilityCritiqueModel] = useState('gpt-5.4')
  const [postQualityAccessibilityGenerateModel, setPostQualityAccessibilityGenerateModel] = useState('gemini-3.1-flash-lite-image')
  const [variantCritiqueModel, setVariantCritiqueModel] = useState('gpt-5.4')
  const [variantCorrectionModel, setVariantCorrectionModel] = useState('gemini-3.1-flash-lite-image')
  const [qualityGateModel, setQualityGateModel] = useState('gpt-4o-mini')
  const [imageAspectRatio, setImageAspectRatio] = useState('4:3')
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
  const selectedWordSourceConfig = wordSources.find((source) => source.table_name === selectedWordSource)
  const selectedWordSourceAvailable = Boolean(selectedWordSourceConfig?.available)

  const validateCsvDagSelections = () => {
    if (!form.person_gender_options.length || !form.person_age_options.length || !form.person_skin_color_options.length) {
      setMessage('For CSV DAG, choose at least one gender, one age, and one skin color')
      return false
    }
    return true
  }

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
        if (mounted && (config?.stage3_anatomy_critique_model || config?.stage3_critique_model || config?.openai_model_vision)) {
          setStage3AnatomyCritiqueModel(config.stage3_anatomy_critique_model || config.stage3_critique_model || config.openai_model_vision)
        }
        if (mounted && (config?.stage3_accessibility_critique_model || config?.stage3_anatomy_critique_model || config?.stage3_critique_model || config?.openai_model_vision)) {
          setStage3AccessibilityCritiqueModel(
            config.stage3_accessibility_critique_model || config.stage3_anatomy_critique_model || config.stage3_critique_model || config.openai_model_vision
          )
        }
        if (mounted && config?.stage3_generate_model) {
          setStage3GenerateModel(config.stage3_generate_model)
        }
        if (mounted && (config?.post_quality_accessibility_critique_model || config?.stage3_accessibility_critique_model || config?.stage3_critique_model || config?.openai_model_vision)) {
          setPostQualityAccessibilityCritiqueModel(
            config.post_quality_accessibility_critique_model || config.stage3_accessibility_critique_model || config.stage3_critique_model || config.openai_model_vision
          )
        }
        if (mounted && (config?.post_quality_accessibility_generate_model || config?.stage3_generate_model)) {
          setPostQualityAccessibilityGenerateModel(config.post_quality_accessibility_generate_model || config.stage3_generate_model)
        }
        if (mounted && (config?.variant_critique_model || config?.stage3_critique_model || config?.openai_model_vision)) {
          setVariantCritiqueModel(config.variant_critique_model || config.stage3_critique_model || config.openai_model_vision)
        }
        if (mounted && (config?.variant_correction_model || config?.stage3_generate_model)) {
          setVariantCorrectionModel(config.variant_correction_model || config.stage3_generate_model)
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

  useEffect(() => {
    if (!csvActivityStartedAt) {
      setCsvActivityElapsedSeconds(0)
      return undefined
    }
    const updateElapsed = () => {
      setCsvActivityElapsedSeconds(Math.max(0, Math.floor((Date.now() - csvActivityStartedAt) / 1000)))
    }
    updateElapsed()
    const timer = window.setInterval(updateElapsed, 1000)
    return () => window.clearInterval(timer)
  }, [csvActivityStartedAt])

  useEffect(() => {
    let mounted = true
    listWordSources()
      .then((sources) => {
        if (!mounted) return
        const approved = Array.isArray(sources) ? sources : []
        setWordSources(approved)
        const firstAvailable = approved.find((source) => source.available)
        if (firstAvailable && !approved.some((source) => source.table_name === selectedWordSource && source.available)) {
          setSelectedWordSource(firstAvailable.table_name)
        }
      })
      .catch(() => {
        if (mounted) setWordSources([])
      })
    return () => {
      mounted = false
    }
  }, [])

  const beginCsvActivity = (label, hint = 'Large CSV files can take a bit longer. The page is still working while this timer moves.') => {
    setCsvActivity({ active: true, label, hint })
    setCsvActivityStartedAt(Date.now())
  }

  const endCsvActivity = () => {
    setCsvActivity((current) => ({ ...current, active: false }))
    setCsvActivityStartedAt(null)
  }

  const formatElapsed = (totalSeconds) => {
    if (!totalSeconds) return '0s'
    const minutes = Math.floor(totalSeconds / 60)
    const seconds = totalSeconds % 60
    if (!minutes) return `${seconds}s`
    return `${minutes}m ${seconds}s`
  }

  const formatFileSize = (sizeBytes) => {
    if (!Number.isFinite(sizeBytes) || sizeBytes <= 0) return '0 B'
    if (sizeBytes < 1024) return `${sizeBytes} B`
    if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`
    return `${(sizeBytes / (1024 * 1024)).toFixed(2)} MB`
  }

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

  const uploadCsvFile = async (file, { sample = false } = {}) => {
    if (!file) {
      setMessage('Choose a CSV file first')
      return
    }
    if (csvExecutionMode === 'csv_dag' && !validateCsvDagSelections()) return
    beginCsvActivity(sample ? 'Importing sample CSV...' : `Uploading ${file.name}...`)
    setMessage(sample ? 'Loading and importing sample CSV...' : `Uploading ${file.name}...`)
    try {
      if (csvExecutionMode === 'csv_dag') {
        const result = await importCsvJob(file, {
          execution_mode: 'csv_dag',
          person_gender_options: form.person_gender_options,
          person_age_options: form.person_age_options,
          person_skin_color_options: form.person_skin_color_options,
          override_existing_variants: overrideExistingVariants,
        })
        setUploadResult({ ...result, mode: 'csv_dag' })
        setSelectedCsvFile(null)
        endCsvActivity()
        setMessage(
          sample
            ? `Imported sample CSV into DAG job ${result.batch_id}`
            : `Imported ${result.imported_count} rows into DAG job ${result.batch_id}`
        )
      } else {
        const result = await importCsv(file)
        setUploadResult({ ...result, mode: 'legacy' })
        setSelectedCsvFile(null)
        endCsvActivity()
        setMessage(
          sample
            ? (
                result.batch_id
                  ? `Imported sample CSV into job ${result.batch_id}`
                  : 'Imported sample CSV'
              )
            : (
                result.batch_id
                  ? `Imported ${result.imported_count} rows into job ${result.batch_id}`
                  : `Imported ${result.imported_count} rows`
              )
        )
      }
    } catch (error) {
      endCsvActivity()
      setMessage(`Error: ${error.message}`)
    }
  }

  const onCsvFileSelected = (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    setSelectedCsvFile(file)
    setUploadResult(null)
    setMessage(`Selected ${file.name}. Click Upload CSV to start importing.`)
    event.target.value = ''
  }

  const onUseSampleCsv = async () => {
    if (csvExecutionMode === 'csv_dag' && !validateCsvDagSelections()) return
    beginCsvActivity('Loading sample CSV...')
    setMessage('Loading sample CSV...')
    try {
      const response = await fetch(SAMPLE_CSV_URL)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const blob = await response.blob()
      const file = new File([blob], SAMPLE_CSV_NAME, { type: 'text/csv' })
      await uploadCsvFile(file, { sample: true })
    } catch (error) {
      endCsvActivity()
      setMessage(`Error: ${error.message}`)
    }
  }

  const loadWordSourceRows = async () => {
    if (!selectedWordSource) return
    setWordSourceLoading(true)
    setMessage(`Loading ${selectedWordSource} from Supabase...`)
    try {
      const result = await listWordSourceRows(selectedWordSource, {
        search: wordSourceSearch,
        limit: 200,
      })
      setWordSourceRows(Array.isArray(result.rows) ? result.rows : [])
      setWordSourceTotal(Number(result.total || 0))
      setSelectedWordSourceRowIds([])
      setMessage(`Loaded ${result.rows?.length || 0} of ${result.total || 0} rows from ${selectedWordSource}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    } finally {
      setWordSourceLoading(false)
    }
  }

  const toggleWordSourceRow = (rowId) => {
    setSelectedWordSourceRowIds((current) => (
      current.includes(rowId)
        ? current.filter((id) => id !== rowId)
        : [...current, rowId]
    ))
  }

  const toggleAllVisibleWordSourceRows = () => {
    const visibleIds = wordSourceRows.map((row) => row.id)
    const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedWordSourceRowIds.includes(id))
    setSelectedWordSourceRowIds(allSelected ? [] : visibleIds)
  }

  const importSelectedWordSourceRows = async () => {
    if (!validateCsvDagSelections()) return
    if (!selectedWordSourceRowIds.length) {
      setMessage('Select at least one word from the Supabase table')
      return
    }
    beginCsvActivity(`Importing ${selectedWordSourceRowIds.length} words from ${selectedWordSource}...`)
    try {
      const result = await importWordSourceRows(selectedWordSource, {
        row_ids: selectedWordSourceRowIds,
        person_gender_options: form.person_gender_options,
        person_age_options: form.person_age_options,
        person_skin_color_options: form.person_skin_color_options,
        override_existing_variants: overrideExistingVariants,
      })
      setUploadResult({ ...result, mode: 'csv_dag', source_table: selectedWordSource })
      setSelectedWordSourceRowIds([])
      setMessage(`Imported ${result.imported_count} words from ${selectedWordSource} into DAG job ${result.batch_id}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    } finally {
      endCsvActivity()
    }
  }

  const onQueueImported = async () => {
    if (!uploadResult) return
    if (uploadResult.mode === 'csv_dag') {
      beginCsvActivity('Starting CSV DAG job...', 'We are queueing the imported words now. This can take a little longer right after a large import.')
      setMessage('Starting CSV DAG job...')
      try {
        const result = await startCsvJob(uploadResult.job_id)
        setUploadResult((current) => (current ? { ...current, status: result.status } : current))
        endCsvActivity()
        setMessage(`Started CSV DAG job ${result.job_id}`)
      } catch (error) {
        endCsvActivity()
        setMessage(`Error: ${error.message}`)
      }
      return
    }
    const entryIds = uploadResult.rows.filter((r) => r.entry_id).map((r) => r.entry_id)
    if (!entryIds.length) {
      setMessage('No valid rows to queue')
      return
    }
    beginCsvActivity('Queueing imported rows...', 'We are applying the selected profile settings and creating runs for the imported rows.')
    setMessage('Applying current person variants and queueing imported entries...')
    try {
      await applyEntryProfileOptions({
        entry_ids: entryIds,
        person_gender_options: form.person_gender_options,
        person_age_options: form.person_age_options,
        person_skin_color_options: form.person_skin_color_options,
      })
      const runs = await createRuns({ entry_ids: entryIds })
      endCsvActivity()
      setMessage(`Queued ${runs.length} runs with the current person variant settings`)
    } catch (error) {
      endCsvActivity()
      setMessage(`Error: ${error.message}`)
    }
  }

  const onSaveWorkerConfig = async () => {
    const parsedRuns = Number(runWorkerCount)
    const parsedVariants = Number(variantWorkerCount)
    if (!Number.isInteger(parsedRuns) || parsedRuns < 1) {
      setMessage('Run workers must be a positive integer')
      return
    }
    if (!Number.isInteger(parsedVariants) || parsedVariants < 1) {
      setMessage('Variant workers must be a positive integer')
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
      if (updated.stage3_anatomy_critique_model) setStage3AnatomyCritiqueModel(updated.stage3_anatomy_critique_model)
      if (updated.stage3_accessibility_critique_model) setStage3AccessibilityCritiqueModel(updated.stage3_accessibility_critique_model)
      if (updated.stage3_generate_model) setStage3GenerateModel(updated.stage3_generate_model)
      if (updated.post_quality_accessibility_critique_model) setPostQualityAccessibilityCritiqueModel(updated.post_quality_accessibility_critique_model)
      if (updated.post_quality_accessibility_generate_model) setPostQualityAccessibilityGenerateModel(updated.post_quality_accessibility_generate_model)
      if (updated.variant_critique_model) setVariantCritiqueModel(updated.variant_critique_model)
      if (updated.variant_correction_model) setVariantCorrectionModel(updated.variant_correction_model)
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
              For CSV DAG, there is no default person profile. Choose the exact gender, age, and skin-color combinations you want to create. The generator will reuse inventory images for the same word when possible and only build missing dependency paths.
            </p>
            <div className="form-grid">
              <div>
                <strong>Applies To Single Concept And Bulk CSV</strong>
              </div>
              <fieldset className="checkbox-group">
                <legend>Gender</legend>
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={form.person_gender_options.includes('male')}
                    onChange={() => toggleOption('person_gender_options', 'male')}
                  />
                  <span>Male</span>
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
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={form.person_age_options.includes('kid')}
                    onChange={() => toggleOption('person_age_options', 'kid')}
                  />
                  <span>Kid (5-9)</span>
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
                  <span>Teenager (20, photorealistic)</span>
                </label>
              </fieldset>
              <p className="config-help-text">
                Teenager variants are rendered as photorealistic 20-year-old outputs. Toddler, kid, and tween variants stay in the illustration style.
              </p>
              <fieldset className="checkbox-group">
                <legend>Skin color</legend>
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={form.person_skin_color_options.includes('white')}
                    onChange={() => toggleOption('person_skin_color_options', 'white')}
                  />
                  <span>White</span>
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
              <label className="checkbox-option">
                <input
                  type="checkbox"
                  checked={overrideExistingVariants}
                  onChange={(e) => setOverrideExistingVariants(e.target.checked)}
                />
                <span>Override existing inventory images for requested variants</span>
              </label>
              <p className="config-help-text">
                Selected combinations: {selectedCombinationCount} requested person profile{selectedCombinationCount === 1 ? '' : 's'}.
                {selectedCombinationCount > generatedProfileCap
                  ? ` The generator will use a capped review set of ${generatedProfileCount} profiles to avoid creating too many images.`
                  : ''}
                {selectedCombinationCount === 0
                  ? ' Choose at least one value in each group before importing a CSV DAG job.'
                  : extraVariantCount > 0
                    ? ` Missing dependency images will be generated automatically, and existing inventory images will be reused unless override is enabled.`
                    : ' Existing inventory images will be reused when available, and only missing requested outputs will be generated.'}
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
                Aspect ratio defaults to `4:3`. Resolution defaults to `1K`. Format defaults to `JPEG`. Safety level maps to Gemini `safetySettings` thresholds for Nano Banana requests.
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
          <h2>Bulk Word Import</h2>
          <p>Choose an approved Supabase table or upload a CSV, then start a dependency-based DAG job using the shared settings above.</p>
          <div className="form-grid">
            <label>
              Word source
              <select
                value={wordSourceType}
                onChange={(e) => {
                  setWordSourceType(e.target.value)
                  setUploadResult(null)
                }}
              >
                <option value="csv">CSV file</option>
                <option value="supabase">Approved Supabase table</option>
              </select>
            </label>
          </div>
          {wordSourceType === 'csv' ? (
            <>
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
              <input type="file" accept=".csv,text/csv" onChange={onCsvFileSelected} />
            </label>
              </div>
              <div className="csv-upload-actions">
            <button type="button" onClick={() => uploadCsvFile(selectedCsvFile)} disabled={!selectedCsvFile || csvActivity.active}>
              {csvActivity.active ? 'Uploading...' : 'Upload CSV'}
            </button>
            {selectedCsvFile ? (
              <p className="csv-upload-selected">
                Selected file: <strong>{selectedCsvFile.name}</strong> ({formatFileSize(selectedCsvFile.size)})
              </p>
            ) : (
              <p className="csv-upload-selected">Choose a local CSV first, then click Upload CSV to start importing it.</p>
            )}
              </div>
              <div className="inline-fields">
            <button type="button" onClick={onUseSampleCsv} disabled={csvActivity.active}>Use Sample CSV</button>
            <a href={SAMPLE_CSV_URL} download={SAMPLE_CSV_NAME}>Download sample CSV</a>
              </div>
            </>
          ) : (
            <div className="word-source-panel">
              <div className="form-grid">
                <label>
                  Approved table
                  <select
                    value={selectedWordSource}
                    onChange={(e) => {
                      setSelectedWordSource(e.target.value)
                      setWordSourceRows([])
                      setSelectedWordSourceRowIds([])
                    }}
                  >
                    {(wordSources.length ? wordSources : [{ table_name: 'word_inventory', label: 'word_inventory', available: false }]).map((source) => (
                      <option key={source.table_name} value={source.table_name} disabled={!source.available}>
                        {source.label}{source.available ? '' : ' (not configured)'}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Search words
                  <input
                    value={wordSourceSearch}
                    onChange={(e) => setWordSourceSearch(e.target.value)}
                    placeholder="Word, part of sentence, or category"
                  />
                </label>
              </div>
              <div className="inline-fields">
                <button type="button" onClick={loadWordSourceRows} disabled={!selectedWordSourceAvailable || wordSourceLoading || csvActivity.active}>
                  {wordSourceLoading ? 'Loading...' : 'Load words'}
                </button>
                <span>{wordSourceRows.length} shown · {wordSourceTotal} total</span>
              </div>
              {!selectedWordSourceAvailable ? (
                <p className="config-help-text">Configure INVENTORY_DATABASE_URL to enable the approved Supabase word source.</p>
              ) : null}
              {wordSourceRows.length ? (
                <div className="table-wrap word-source-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>
                          <input
                            type="checkbox"
                            aria-label="Select all visible words"
                            checked={wordSourceRows.every((row) => selectedWordSourceRowIds.includes(row.id))}
                            onChange={toggleAllVisibleWordSourceRows}
                          />
                        </th>
                        <th>Word</th>
                        <th>Part of sentence</th>
                        <th>Category</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {wordSourceRows.map((row) => (
                        <tr key={row.id}>
                          <td>
                            <input
                              type="checkbox"
                              aria-label={`Select ${row.word}`}
                              checked={selectedWordSourceRowIds.includes(row.id)}
                              onChange={() => toggleWordSourceRow(row.id)}
                            />
                          </td>
                          <td>{row.word}</td>
                          <td>{row.part_of_sentence}</td>
                          <td>{row.category || '-'}</td>
                          <td>{row.fully_complete ? 'Complete' : (row.job_status || 'Pending')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
              <button
                type="button"
                onClick={importSelectedWordSourceRows}
                disabled={!selectedWordSourceRowIds.length || csvActivity.active}
              >
                Import {selectedWordSourceRowIds.length || ''} selected word{selectedWordSourceRowIds.length === 1 ? '' : 's'}
              </button>
              <p className="config-help-text">
                Generated paths, prompts, status, and completion fields will be written back to the selected word_inventory rows.
              </p>
            </div>
          )}
          {csvActivity.active ? (
            <div className="csv-upload-progress" role="status" aria-live="polite">
              <div className="csv-upload-progress-header">
                <span className="csv-upload-spinner" aria-hidden="true" />
                <strong>{csvActivity.label}</strong>
                <span>{formatElapsed(csvActivityElapsedSeconds)} elapsed</span>
              </div>
              <p>{csvActivity.hint}</p>
            </div>
          ) : null}
          <button onClick={onQueueImported} disabled={!uploadResult || csvActivity.active}>
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
          <p>Run workers control whole-word concurrency, including CSV DAG jobs. Variant workers control image fanout inside each word.</p>
          <div className="inline-fields">
            <label>
              Run workers
              <input
                type="number"
                min="1"
                value={runWorkerCount}
                onChange={(e) => setRunWorkerCount(e.target.value)}
              />
            </label>
            <label>
              Variant workers
              <input
                type="number"
                min="1"
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
          <p>Choose models for Stage 3 critique, Stage 3.15 anatomy critique, the disabled legacy Stage 3.16 control, the post-quality AAC softening steps, Stage 3 upgraded image, the variant review/correction steps, and Quality Gate scoring. Changes are saved automatically.</p>
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
                <option value="gpt-5.4-mini">gpt-5.4-mini</option>
                <option value="gpt-5.4-nano">gpt-5.4-nano</option>
                <option value="gemini-3.1-pro-preview">gemini-3.1-pro-preview</option>
                <option value="gemini-3-flash-preview">gemini-3-flash-preview</option>
                <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite</option>
              </select>
            </label>
            <label>
              Stage 3.15 Anatomy Critique
              <select
                value={stage3AnatomyCritiqueModel}
                onChange={(e) => {
                  const value = e.target.value
                  setStage3AnatomyCritiqueModel(value)
                  setMessage('Saving Stage 3 anatomy critique model...')
                  saveModelConfig({ stage3_anatomy_critique_model: value }, `Saved Stage 3 anatomy critique model: ${value}`)
                }}
              >
                <option value="gpt-4o-mini">gpt-4o-mini</option>
                <option value="gpt-5.4">gpt-5.4</option>
                <option value="gpt-5.4-mini">gpt-5.4-mini</option>
                <option value="gpt-5.4-nano">gpt-5.4-nano</option>
                <option value="gemini-3.1-pro-preview">gemini-3.1-pro-preview</option>
                <option value="gemini-3-flash-preview">gemini-3-flash-preview</option>
                <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite</option>
              </select>
            </label>
            <label>
              Stage 3.16 Simplicity Critique (disabled)
              <select
                value={stage3AccessibilityCritiqueModel}
                disabled
              >
                <option value="gpt-4o-mini">gpt-4o-mini</option>
                <option value="gpt-5.4">gpt-5.4</option>
                <option value="gpt-5.4-mini">gpt-5.4-mini</option>
                <option value="gpt-5.4-nano">gpt-5.4-nano</option>
                <option value="gemini-3.1-pro-preview">gemini-3.1-pro-preview</option>
                <option value="gemini-3-flash-preview">gemini-3-flash-preview</option>
                <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite</option>
              </select>
              <span className="field-help-text">Shown for backward compatibility only. New runs skip this step.</span>
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
                <option value="gemini-3.1-flash-lite-image">Gemini 3.1 Flash Lite Image</option>
                <option value="flux-1.1-pro">Flux 1.1 Pro</option>
                <option value="imagen-3">Imagen 3</option>
                <option value="imagen-4">Imagen 4</option>
                <option value="nano-banana">Nano Banana</option>
                <option value="nano-banana-2">Nano Banana 2</option>
                <option value="nano-banana-pro">Nano Banana Pro</option>
              </select>
            </label>
            <label>
              Post-quality AAC Critique
              <select
                value={postQualityAccessibilityCritiqueModel}
                onChange={(e) => {
                  const value = e.target.value
                  setPostQualityAccessibilityCritiqueModel(value)
                  setMessage('Saving post-quality AAC critique model...')
                  saveModelConfig({ post_quality_accessibility_critique_model: value }, `Saved post-quality AAC critique model: ${value}`)
                }}
              >
                <option value="gpt-4o-mini">gpt-4o-mini</option>
                <option value="gpt-5.4">gpt-5.4</option>
                <option value="gpt-5.4-mini">gpt-5.4-mini</option>
                <option value="gpt-5.4-nano">gpt-5.4-nano</option>
                <option value="gemini-3.1-pro-preview">gemini-3.1-pro-preview</option>
                <option value="gemini-3-flash-preview">gemini-3-flash-preview</option>
                <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite</option>
              </select>
            </label>
            <label>
              Post-quality AAC Soften Image
              <select
                value={postQualityAccessibilityGenerateModel}
                onChange={(e) => {
                  const value = e.target.value
                  setPostQualityAccessibilityGenerateModel(value)
                  setMessage('Saving post-quality AAC soften image model...')
                  saveModelConfig({ post_quality_accessibility_generate_model: value }, `Saved post-quality AAC soften image model: ${value}`)
                }}
              >
                <option value="gemini-3.1-flash-lite-image">Gemini 3.1 Flash Lite Image</option>
                <option value="flux-1.1-pro">Flux 1.1 Pro</option>
                <option value="imagen-3">Imagen 3</option>
                <option value="imagen-4">Imagen 4</option>
                <option value="nano-banana">Nano Banana</option>
                <option value="nano-banana-2">Nano Banana 2</option>
                <option value="nano-banana-pro">Nano Banana Pro</option>
              </select>
            </label>
            <label>
              Step 8.1 Variant Critique
              <select
                value={variantCritiqueModel}
                onChange={(e) => {
                  const value = e.target.value
                  setVariantCritiqueModel(value)
                  setMessage('Saving variant critique model...')
                  saveModelConfig({ variant_critique_model: value }, `Saved variant critique model: ${value}`)
                }}
              >
                <option value="gpt-4o-mini">gpt-4o-mini</option>
                <option value="gpt-5.4">gpt-5.4</option>
                <option value="gpt-5.4-mini">gpt-5.4-mini</option>
                <option value="gpt-5.4-nano">gpt-5.4-nano</option>
                <option value="gemini-3.1-pro-preview">gemini-3.1-pro-preview</option>
                <option value="gemini-3-flash-preview">gemini-3-flash-preview</option>
                <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite</option>
              </select>
            </label>
            <label>
              Step 8.2 Variant Correction
              <select
                value={variantCorrectionModel}
                onChange={(e) => {
                  const value = e.target.value
                  setVariantCorrectionModel(value)
                  setMessage('Saving variant correction model...')
                  saveModelConfig({ variant_correction_model: value }, `Saved variant correction model: ${value}`)
                }}
              >
                <option value="gemini-3.1-flash-lite-image">Gemini 3.1 Flash Lite Image</option>
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
                <option value="gpt-5.4-mini">gpt-5.4-mini</option>
                <option value="gpt-5.4-nano">gpt-5.4-nano</option>
                <option value="gemini-3.1-pro-preview">gemini-3.1-pro-preview</option>
                <option value="gemini-3-flash-preview">gemini-3-flash-preview</option>
                <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite</option>
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
                <option value="gpt-5.4-mini">gpt-5.4-mini</option>
                <option value="gpt-5.4-nano">gpt-5.4-nano</option>
                <option value="gemini-3.1-pro-preview">gemini-3.1-pro-preview</option>
                <option value="gemini-3-flash-preview">gemini-3-flash-preview</option>
                <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite</option>
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
