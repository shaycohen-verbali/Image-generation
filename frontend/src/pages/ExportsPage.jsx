import React, { useEffect, useMemo, useState } from 'react'
import { buildApiUrl, createExport, downloadWordSourceReport, exportCsvJob, exportWordSourceRows, getCloudflareConfig, getCloudflareUploadStatus, listCsvJobs, listRuns, listWordSourceRows } from '../lib/api'

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

const CSV_JOB_EXPORT_BASE_FIELDS = [
  'row_index',
  'word',
  'part_of_sentence',
  'category',
  'context',
  'job_status',
  'fully_complete',
  'missing_slots_json',
  'failure_reasons_json',
]

const CSV_JOB_AGE_OPTIONS = [
  { value: 'all', label: 'All ages' },
  { value: 'toddler', label: 'Toddler' },
  { value: 'kid', label: 'Kid' },
  { value: 'tween', label: 'Tween' },
  { value: 'teenager', label: 'Teenager' },
]

const CSV_JOB_GENDER_OPTIONS = [
  { value: 'all', label: 'All genders' },
  { value: 'male', label: 'Male' },
  { value: 'female', label: 'Female' },
]

const CSV_JOB_RACE_OPTIONS = [
  { value: 'all', label: 'All races' },
  { value: 'white', label: 'White' },
  { value: 'black', label: 'Black' },
  { value: 'asian', label: 'Asian' },
  { value: 'brown', label: 'Brown' },
]

const CSV_JOB_EXPORT_ALL_AGES = CSV_JOB_AGE_OPTIONS.filter((item) => item.value !== 'all').map((item) => item.value)
const CSV_JOB_EXPORT_ALL_GENDERS = CSV_JOB_GENDER_OPTIONS.filter((item) => item.value !== 'all').map((item) => item.value)
const CSV_JOB_EXPORT_ALL_RACES = CSV_JOB_RACE_OPTIONS.filter((item) => item.value !== 'all').map((item) => item.value)

const MATALK_TABLE_DOWNLOAD_LABELS = {
  aac_dictionary: 'Download aac_dictionary.csv',
  aac_image_meta: 'Download aac_image_meta.csv',
  aac_images: 'Download aac_images.csv',
  manifest: 'Download MaTalk manifest',
}

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

function csvExportFieldsFromSelection({ age, gender, race, includePrompt, includeWhiteBackground }) {
  const ages = age === 'all' ? CSV_JOB_EXPORT_ALL_AGES : [age]
  const genders = gender === 'all' ? CSV_JOB_EXPORT_ALL_GENDERS : [gender]
  const races = race === 'all' ? CSV_JOB_EXPORT_ALL_RACES : [race]
  const fields = [...CSV_JOB_EXPORT_BASE_FIELDS]

  for (const ageValue of ages) {
    for (const genderValue of genders) {
      for (const raceValue of races) {
        const base = `${ageValue}_${genderValue}_${raceValue}`
        fields.push(`${base}_regular_path`)
        if (includeWhiteBackground) fields.push(`${base}_white_bg_path`)
        if (includePrompt) {
          fields.push(`${base}_regular_prompt`)
          if (includeWhiteBackground) fields.push(`${base}_white_bg_prompt`)
        }
      }
    }
  }

  return fields
}

function csvExportSummary({ age, gender, race, includePrompt, includeWhiteBackground }) {
  return [
    `age: ${age}`,
    `gender: ${gender}`,
    `race: ${race}`,
    `include prompt: ${includePrompt ? 'yes' : 'no'}`,
    `include white background: ${includeWhiteBackground ? 'yes' : 'no'}`,
  ].join(' | ')
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
  const [selectedLegacyExportFields, setSelectedLegacyExportFields] = useState(() => LEGACY_EXPORT_FIELD_OPTIONS.map((item) => item.key))
  const [selectedCsvExportAge, setSelectedCsvExportAge] = useState('all')
  const [selectedCsvExportGender, setSelectedCsvExportGender] = useState('all')
  const [selectedCsvExportRace, setSelectedCsvExportRace] = useState('all')
  const [includeCsvExportPrompt, setIncludeCsvExportPrompt] = useState(false)
  const [includeCsvExportWhiteBackground, setIncludeCsvExportWhiteBackground] = useState(true)
  const [inventorySelectionMode, setInventorySelectionMode] = useState('last_job')
  const [inventoryRangeStart, setInventoryRangeStart] = useState(1)
  const [inventoryRangeEnd, setInventoryRangeEnd] = useState(100)
  const [inventorySearch, setInventorySearch] = useState('')
  const [inventoryRows, setInventoryRows] = useState([])
  const [selectedInventoryRowId, setSelectedInventoryRowId] = useState('')
  const [inventoryLoading, setInventoryLoading] = useState(false)
  const [inventoryDestination, setInventoryDestination] = useState('zip')
  const [cloudflareBuckets, setCloudflareBuckets] = useState([])
  const [cloudflareBucket, setCloudflareBucket] = useState('matalkimages')
  const [cloudflareQuality, setCloudflareQuality] = useState(79)
  const [convertToMatalkTablesFormat, setConvertToMatalkTablesFormat] = useState(false)

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
    getCloudflareConfig().then((config) => {
      setCloudflareBuckets(config.buckets || [])
      setCloudflareBucket(config.default_bucket || config.buckets?.[0] || 'matalkimages')
      setCloudflareQuality(config.compression_quality || 79)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (sourceMode === 'legacy_run' && !runs.length && csvJobs.length) {
      setSourceMode('csv_job')
    }
    if (sourceMode === 'csv_job' && !csvJobs.length && runs.length) {
      setSourceMode('legacy_run')
    }
  }, [sourceMode, runs.length, csvJobs.length])

  const loadInventoryRows = async () => {
    setInventoryLoading(true)
    try {
      const result = await listWordSourceRows('word_inventory', {
        search: inventorySearch,
        selection_mode: 'all',
        limit: 200,
      })
      setInventoryRows(result.rows || [])
      setSelectedInventoryRowId('')
      setMessage(`Matched ${result.total || 0} word_inventory rows; showing the first ${result.rows?.length || 0}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    } finally {
      setInventoryLoading(false)
    }
  }

  const create = async () => {
    setMessage(sourceMode === 'csv_job' ? 'Preparing CSV DAG package...' : sourceMode === 'word_inventory' ? (inventoryDestination === 'cloudflare' ? 'Fetching selected Supabase data, compressing images, and uploading to Cloudflare...' : 'Fetching selected Supabase data and images, then building the ZIP...') : 'Preparing legacy export package...')
    try {
      if (sourceMode === 'csv_job') {
        if (!selectedCsvJobId) {
          setMessage('Select a CSV job first')
          return
        }
        const csvExportOptions = {
          age: selectedCsvExportAge,
          gender: selectedCsvExportGender,
          race: selectedCsvExportRace,
          includePrompt: includeCsvExportPrompt,
          includeWhiteBackground: includeCsvExportWhiteBackground,
        }
        const result = await exportCsvJob(selectedCsvJobId, {
          export_fields: csvExportFieldsFromSelection(csvExportOptions),
          convert_to_matalk_tables_format: convertToMatalkTablesFormat,
        })
        const nextPrepared = {
          kind: 'csv_job',
          id: result.job_id,
          batch_id: result.batch_id,
          file_name: result.file_name,
          package_download_url: result.download_url,
          export_summary: `${csvExportSummary(csvExportOptions)}${convertToMatalkTablesFormat ? ' | MaTalk AI tables' : ''}`,
          matalk_download_urls: result.matalk_download_urls || {},
          matalk_row_counts: result.matalk_row_counts || {},
          matalk_warnings: result.matalk_warnings || [],
        }
        setPreparedExport(nextPrepared)
        setMessage(`Prepared CSV job package for ${result.job_id}`)
        triggerDownload(result.download_url)
        return
      }

      if (sourceMode === 'word_inventory') {
        if (inventorySelectionMode === 'single' && !selectedInventoryRowId) {
          setMessage('Choose a specific word + POS first')
          return
        }
        const csvExportOptions = {
          age: selectedCsvExportAge,
          gender: selectedCsvExportGender,
          race: selectedCsvExportRace,
          includePrompt: includeCsvExportPrompt,
          includeWhiteBackground: includeCsvExportWhiteBackground,
        }
        const result = await exportWordSourceRows('word_inventory', {
          selection_mode: inventorySelectionMode,
          row_id: inventorySelectionMode === 'single' ? selectedInventoryRowId : undefined,
          range_start: inventorySelectionMode === 'range' ? Number(inventoryRangeStart) : undefined,
          range_end: inventorySelectionMode === 'range' ? Number(inventoryRangeEnd) : undefined,
          export_fields: csvExportFieldsFromSelection(csvExportOptions),
          destination: inventoryDestination,
          cloudflare_bucket: inventoryDestination === 'cloudflare' ? cloudflareBucket : undefined,
          compression_quality: Number(cloudflareQuality),
          convert_to_matalk_tables_format: convertToMatalkTablesFormat,
        })
        if (inventoryDestination === 'cloudflare') {
          setPreparedExport({ kind: 'cloudflare', ...result, export_summary: inventorySelectionMode.replaceAll('_', ' ') })
          setMessage(`Cloudflare upload started: ${result.row_count} selected rows contain ${result.total} images; processing is running on Render`)
          for (let attempt = 0; attempt < 120; attempt += 1) {
            await new Promise((resolve) => window.setTimeout(resolve, 3000))
            const status = await getCloudflareUploadStatus(result.batch_id)
            setPreparedExport((current) => current ? { ...current, ...status } : current)
            if (status.status === 'completed' || status.status === 'completed_with_errors' || status.status === 'failed') {
              setMessage(`Cloudflare upload ${status.status}: ${status.uploaded} uploaded, ${status.skipped} skipped, ${status.failed} failed${status.error_detail ? ` — ${status.error_detail}` : ''}`)
              break
            }
          }
          return
        }
        setPreparedExport({
          kind: 'inventory',
          id: result.job_id,
          batch_id: result.batch_id,
          file_name: result.file_name,
          package_download_url: result.download_url,
          export_summary: `${inventorySelectionMode.replaceAll('_', ' ')} | ${csvExportSummary(csvExportOptions)}${convertToMatalkTablesFormat ? ' | MaTalk AI tables' : ''}`,
          matalk_download_urls: result.matalk_download_urls || {},
          matalk_row_counts: result.matalk_row_counts || {},
          matalk_warnings: result.matalk_warnings || [],
        })
        setMessage('Prepared word_inventory package')
        triggerDownload(result.download_url)
        return
      }

      const payload = {}
      if (statusFilter) payload.status = [statusFilter]
      if (selectedRunId) payload.run_ids = [selectedRunId]
      payload.export_fields = selectedLegacyExportFields
      if (!selectedLegacyExportFields.length) {
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

  const toggleLegacyExportField = (fieldKey) => {
    setSelectedLegacyExportFields((current) =>
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
            <select value={sourceMode} onChange={(e) => {
              const nextSource = e.target.value
              setSourceMode(nextSource)
              if (nextSource === 'legacy_run') setConvertToMatalkTablesFormat(false)
            }}>
              <option value="csv_job">CSV DAG job package</option>
              <option value="word_inventory">word_inventory</option>
              <option value="legacy_run">Legacy run bundle</option>
            </select>
          </label>

          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
              <input
                type="checkbox"
                checked={convertToMatalkTablesFormat}
                disabled={sourceMode === 'legacy_run'}
                onChange={(e) => setConvertToMatalkTablesFormat(e.target.checked)}
              />
              <span>Convert to MaTalk AI tables format in Neon</span>
            </label>
            {sourceMode === 'legacy_run' ? (
              <p className="config-help-text">This option needs the word inventory fields and is not available for legacy run bundles.</p>
            ) : sourceMode === 'word_inventory' && inventoryDestination === 'cloudflare' ? (
              <p className="config-help-text">When checked, MaTalk CSVs are prepared after the images finish uploading. Their <code>image_url</code> values contain the public remote location and filename.</p>
            ) : (
              <p className="config-help-text">Adds <code>aac_dictionary.csv</code>, <code>aac_image_meta.csv</code>, and <code>aac_images.csv</code> to the package. It does not write to Neon automatically.</p>
            )}
          </div>

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
              <div>
                <p className="config-help-text">
                  Choose which variants to include in the CSV DAG package. The package includes <code>images.csv</code>, <code>prompts.csv</code>, and organized image folders.
                </p>
                <div className="form-grid">
                  <label>
                    Age
                    <select value={selectedCsvExportAge} onChange={(e) => setSelectedCsvExportAge(e.target.value)}>
                      {CSV_JOB_AGE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Gender
                    <select value={selectedCsvExportGender} onChange={(e) => setSelectedCsvExportGender(e.target.value)}>
                      {CSV_JOB_GENDER_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Race
                    <select value={selectedCsvExportRace} onChange={(e) => setSelectedCsvExportRace(e.target.value)}>
                      {CSV_JOB_RACE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="inline-fields">
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                    <input
                      type="checkbox"
                      checked={includeCsvExportPrompt}
                      onChange={(e) => setIncludeCsvExportPrompt(e.target.checked)}
                    />
                    <span>Include prompt</span>
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                    <input
                      type="checkbox"
                      checked={includeCsvExportWhiteBackground}
                      onChange={(e) => setIncludeCsvExportWhiteBackground(e.target.checked)}
                    />
                    <span>Include white background images for chosen variants</span>
                  </label>
                </div>
                <p className="config-help-text">
                  Images are grouped into <code>images/regular</code> and <code>images/white_background</code>. Legacy debug files stay under <code>_metadata</code>.
                </p>
              </div>
            </>
          ) : sourceMode === 'word_inventory' ? (
            <>
              <label>
                Export selection
                <select value={inventorySelectionMode} onChange={(e) => setInventorySelectionMode(e.target.value)}>
                  <option value="last_job">Last job</option>
                  <option value="range">Range of words</option>
                  <option value="single">Specific word + POS</option>
                  <option value="all">All info in the table</option>
                </select>
              </label>
              {inventorySelectionMode === 'range' ? (
                <div className="form-grid">
                  <label>First word position<input type="number" min="1" value={inventoryRangeStart} onChange={(e) => setInventoryRangeStart(Math.max(1, Number(e.target.value) || 1))} /></label>
                  <label>Last word position<input type="number" min="1" value={inventoryRangeEnd} onChange={(e) => setInventoryRangeEnd(Math.max(1, Number(e.target.value) || 1))} /></label>
                </div>
              ) : null}
              {inventorySelectionMode === 'single' ? (
                <div>
                  <div className="inline-fields">
                    <input value={inventorySearch} onChange={(e) => setInventorySearch(e.target.value)} placeholder="Search word or POS" />
                    <button type="button" className="button-secondary" onClick={loadInventoryRows} disabled={inventoryLoading}>{inventoryLoading ? 'Loading…' : 'Find word + POS'}</button>
                  </div>
                  {inventoryRows.length ? (
                    <label>
                      Choose word + POS
                      <select value={selectedInventoryRowId} onChange={(e) => setSelectedInventoryRowId(e.target.value)}>
                        <option value="">Select a word + POS</option>
                        {inventoryRows.map((row) => <option key={row.id} value={row.id}>{`${row.word} · ${row.part_of_speech || row.part_of_sentence}${row.sense_id ? ` · ${row.sense_id}` : ''}`}</option>)}
                      </select>
                    </label>
                  ) : <p className="config-help-text">Search to choose the exact word and part of speech.</p>}
                </div>
              ) : null}
              <div>
                <label>
                  Destination
                  <select value={inventoryDestination} onChange={(e) => {
                      const nextDestination = e.target.value
                      setInventoryDestination(nextDestination)
                    }}>
                    <option value="zip">Download ZIP package</option>
                    <option value="cloudflare">Upload images to Cloudflare R2</option>
                  </select>
                </label>
                {inventoryDestination === 'cloudflare' ? (
                  <>
                    <div className="form-grid">
                      <label>Cloudflare bucket<select value={cloudflareBucket} onChange={(e) => setCloudflareBucket(e.target.value)}>{cloudflareBuckets.length ? cloudflareBuckets.map((bucket) => <option key={bucket} value={bucket}>{bucket}</option>) : <option value="matalkimages">matalkimages</option>}</select></label>
                      <label>JPEG quality<input type="number" min="1" max="100" value={cloudflareQuality} onChange={(e) => setCloudflareQuality(Math.max(1, Math.min(100, Number(e.target.value) || 79)))} /></label>
                    </div>
                    <p className="config-help-text">Every non-empty image variant for the selected words is uploaded: all ages, genders, skin colors, regular images, and white-background images. Prompts and text files are not uploaded. Images are compressed on Render immediately before upload. If MaTalk is checked, its CSVs are prepared after the upload so <code>image_url</code> points to the final public R2 object.</p>
                  </>
                ) : (
                  <>
                    <p className="config-help-text">This fetches the selected rows and images from Supabase. Large ranges download every selected image before the ZIP is ready; ranges start at 100 words to avoid accidental long exports.</p>
                    <div className="form-grid">
                      <label>Age<select value={selectedCsvExportAge} onChange={(e) => setSelectedCsvExportAge(e.target.value)}>{CSV_JOB_AGE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
                      <label>Gender<select value={selectedCsvExportGender} onChange={(e) => setSelectedCsvExportGender(e.target.value)}>{CSV_JOB_GENDER_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
                      <label>Race<select value={selectedCsvExportRace} onChange={(e) => setSelectedCsvExportRace(e.target.value)}>{CSV_JOB_RACE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
                    </div>
                    <div className="inline-fields">
                      <label><input type="checkbox" checked={includeCsvExportPrompt} onChange={(e) => setIncludeCsvExportPrompt(e.target.checked)} /> Include prompt</label>
                      <label><input type="checkbox" checked={includeCsvExportWhiteBackground} onChange={(e) => setIncludeCsvExportWhiteBackground(e.target.checked)} /> Include white background images</label>
                    </div>
                  </>
                )}
                <div className="inline-fields">
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={async () => {
                      if (convertToMatalkTablesFormat) {
                        await create()
                        return
                      }
                      triggerDownload(await downloadWordSourceReport('word_inventory', {
                        selection_mode: inventorySelectionMode,
                        row_id: inventorySelectionMode === 'single' ? selectedInventoryRowId : undefined,
                        range_start: inventorySelectionMode === 'range' ? Number(inventoryRangeStart) : undefined,
                        range_end: inventorySelectionMode === 'range' ? Number(inventoryRangeEnd) : undefined,
                      }))
                    }}
                  >
                    {convertToMatalkTablesFormat ? 'Download MaTalk table CSV package' : 'Download CSV report'}
                  </button>
                </div>
              </div>
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
                    onClick={() => setSelectedLegacyExportFields(LEGACY_EXPORT_FIELD_OPTIONS.map((item) => item.key))}
                  >
                    Select All
                  </button>
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={() => setSelectedLegacyExportFields([])}
                  >
                    Clear All
                  </button>
                  <span className="config-help-text">{selectedLegacyExportFields.length} fields selected</span>
                </div>
                <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
                  {LEGACY_EXPORT_FIELD_OPTIONS.map((field) => (
                    <label key={field.key} style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                      <input
                        type="checkbox"
                        checked={selectedLegacyExportFields.includes(field.key)}
                        onChange={() => toggleLegacyExportField(field.key)}
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
            {sourceMode === 'csv_job' ? (convertToMatalkTablesFormat ? 'Download CSV Job Package + MaTalk Tables' : 'Download CSV Job Package') : sourceMode === 'word_inventory' ? (inventoryDestination === 'cloudflare' ? (convertToMatalkTablesFormat ? 'Upload Images + MaTalk Tables' : 'Upload Images to Cloudflare') : (convertToMatalkTablesFormat ? 'Download Package + MaTalk Tables' : 'Download word_inventory Package')) : 'Create And Download Export'}
          </button>
          <button onClick={refreshData} className="button-secondary">Refresh Lists</button>
        </div>
      </article>

      <article className="card message-card">
        <h2>Status</h2>
        <p>{message || 'No export has been prepared yet.'}</p>

        {preparedExport ? (
          <div className="form-grid">
            {preparedExport.kind === 'cloudflare' ? (
              <>
                <p className="config-help-text"><strong>Upload batch:</strong> <span style={{ wordBreak: 'break-all' }}>{preparedExport.batch_id}</span></p>
                <p className="config-help-text"><strong>Bucket:</strong> {preparedExport.bucket}</p>
                <p className="config-help-text"><strong>Selected rows:</strong> {preparedExport.row_count} · <strong>Images:</strong> {preparedExport.total}</p>
                <p className="config-help-text"><strong>Uploaded:</strong> {preparedExport.uploaded} · <strong>Skipped:</strong> {preparedExport.skipped} · <strong>Failed:</strong> {preparedExport.failed}</p>
                {preparedExport.error_detail ? <p className="config-help-text"><strong>Error:</strong> {preparedExport.error_detail}</p> : null}
                <button type="button" onClick={() => triggerDownload(preparedExport.report_url)}>Download upload history CSV</button>
                {Object.keys(preparedExport.matalk_download_urls || {}).length ? (
                  <>
                    <p className="config-help-text">
                      MaTalk tables prepared for remote images; <code>aac_images.image_url</code> contains the public R2 location and filename.
                      {Object.entries(preparedExport.matalk_row_counts || {}).length ? ` Rows: ${Object.entries(preparedExport.matalk_row_counts).map(([table, count]) => `${table} ${count}`).join(', ')}.` : ''}
                    </p>
                    <div className="inline-fields">
                      {Object.entries(preparedExport.matalk_download_urls).map(([key, url]) => (
                        <button key={key} type="button" className="button-secondary" onClick={() => triggerDownload(url)}>
                          {MATALK_TABLE_DOWNLOAD_LABELS[key] || `Download ${key}`}
                        </button>
                      ))}
                    </div>
                  </>
                ) : null}
                {preparedExport.matalk_warnings?.length ? (
                  <p className="config-help-text"><strong>MaTalk warnings:</strong> {preparedExport.matalk_warnings.join(' ')}</p>
                ) : null}
              </>
            ) : preparedExport.kind === 'csv_job' || preparedExport.kind === 'inventory' ? (
              <>
                <p className="config-help-text"><strong>{preparedExport.kind === 'inventory' ? 'Export job number' : 'CSV job number'}:</strong> <span style={{ wordBreak: 'break-all' }}>{preparedExport.id}</span></p>
                <p className="config-help-text"><strong>Batch number:</strong> <span style={{ wordBreak: 'break-all' }}>{preparedExport.batch_id}</span></p>
                <p className="config-help-text"><strong>File name:</strong> {preparedExport.file_name}</p>
                <p className="config-help-text">
                  <strong>Selection:</strong> {preparedExport.export_summary || 'All default fields'}
                </p>
                <div className="inline-fields">
                  <button type="button" onClick={() => triggerDownload(preparedExport.package_download_url)}>
                    Download Package Again
                  </button>
                </div>
                {Object.keys(preparedExport.matalk_download_urls || {}).length ? (
                  <>
                    <p className="config-help-text">
                      MaTalk tables prepared in import order: dictionary → image metadata → images.
                      {Object.entries(preparedExport.matalk_row_counts || {}).length ? ` Rows: ${Object.entries(preparedExport.matalk_row_counts).map(([table, count]) => `${table} ${count}`).join(', ')}.` : ''}
                    </p>
                    <div className="inline-fields">
                      {Object.entries(preparedExport.matalk_download_urls).map(([key, url]) => (
                        <button key={key} type="button" className="button-secondary" onClick={() => triggerDownload(url)}>
                          {MATALK_TABLE_DOWNLOAD_LABELS[key] || `Download ${key}`}
                        </button>
                      ))}
                    </div>
                    {preparedExport.matalk_warnings?.length ? (
                      <p className="config-help-text"><strong>MaTalk warnings:</strong> {preparedExport.matalk_warnings.join(' ')}</p>
                    ) : null}
                  </>
                ) : null}
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
