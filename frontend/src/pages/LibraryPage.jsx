import React, { useEffect, useMemo, useRef, useState } from 'react'
import { buildApiUrl, getLibraryLemma, listLibraryLemmas, listSenseImages } from '../lib/api'

const POS_OPTIONS = ['All parts of speech', 'noun', 'verb', 'adjective', 'adverb', 'preposition', 'pronoun', 'conjunction']
const AGE_OPTIONS = ['All ages', 'toddler', 'kid', 'tween', 'teenager']
const GENDER_OPTIONS = ['All genders', 'male', 'female']
const SKIN_OPTIONS = ['All skin tones', 'white', 'black', 'asian', 'brown']
const BACKGROUND_OPTIONS = ['All backgrounds', 'regular', 'white_bg']

const PREVIEW_LEMMAS = [
  { lemma: 'abandon', forms: ['abandon', 'abandons', 'abandoned', 'abandoning'], parts_of_speech: ['noun', 'verb'], sense_count: 7, image_count: 2 },
  { lemma: 'abandoned', forms: ['abandoned'], parts_of_speech: ['adjective'], sense_count: 2, image_count: 0 },
  { lemma: 'abandonment', forms: ['abandonment'], parts_of_speech: ['noun'], sense_count: 1, image_count: 0 },
]

const PREVIEW_WORD = {
  lemma: 'abandon',
  observed_forms: ['abandon', 'abandons', 'abandoned', 'abandoning'],
  pos_groups: [
    {
      pos: 'noun',
      senses: [
        { id: 'cc88eab6025a4403', definition: 'the trait of lacking restraint or control; reckless freedom from inhibition or worry', image_count: 2 },
        { id: 'abandon-noun-2', definition: 'a feeling of extreme emotional intensity', image_count: 0 },
      ],
    },
    {
      pos: 'verb',
      senses: [
        { id: 'abandon-verb-1', definition: 'to leave a place, thing, or person permanently', image_count: 0 },
        { id: 'abandon-verb-2', definition: 'to give up an activity or intention', image_count: 0 },
      ],
    },
  ],
}

const PREVIEW_IMAGES = [
  {
    id: 'preview-regular',
    age: 'kid',
    gender: 'male',
    skin_tone: 'white',
    background: 'regular',
    image_url: '',
    original_url: '',
    filename: 'abandon__noun__regular__kid__male__white.jpg',
    prompt: 'Current V1 prompt preview unavailable in the local Library preview. The connected inventory API will show the exact stored prompt here.',
  },
  {
    id: 'preview-white',
    age: 'kid',
    gender: 'male',
    skin_tone: 'white',
    background: 'white_bg',
    image_url: '',
    original_url: '',
    filename: 'abandon__noun__white-background__kid__male__white.jpg',
    prompt: 'Current V1 prompt preview unavailable in the local Library preview. The connected inventory API will show the exact stored prompt here.',
  },
]

function Icon({ name, size = 20, strokeWidth = 1.8 }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }
  const paths = {
    search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
    filter: <><path d="M4 5h16" /><path d="M7 12h10" /><path d="M10 19h4" /></>,
    chevron: <path d="m8 10 4 4 4-4" />,
    arrowRight: <path d="m9 6 6 6-6 6" />,
    arrowLeft: <><path d="m15 18-6-6 6-6" /><path d="M4 12h11" /></>,
    close: <><path d="m6 6 12 12" /><path d="m18 6-12 12" /></>,
    copy: <><rect x="9" y="9" width="10" height="10" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>,
    external: <><path d="M14 4h6v6" /><path d="m20 4-9 9" /><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5" /></>,
    download: <><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></>,
    list: <><path d="M8 6h12" /><path d="M8 12h12" /><path d="M8 18h12" /><path d="M3 6h.01" /><path d="M3 12h.01" /><path d="M3 18h.01" /></>,
    book: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5z" /><path d="M4 5.5v16" /><path d="M8 7h8" /></>,
    image: <><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="8.5" cy="9" r="1.5" /><path d="m4 17 5-5 3 3 2-2 6 6" /></>,
    warning: <><path d="m12 3 9 17H3z" /><path d="M12 9v4" /><path d="M12 17h.01" /></>,
  }
  return <svg {...common}>{paths[name] || paths.image}</svg>
}

function formatLabel(value) {
  return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function normalizeLemma(item) {
  const lemma = item.lemma || item.lemmatized_word || item.word || ''
  return {
    ...item,
    lemma,
    forms: item.forms || item.observed_forms || item.source_words || [item.word || lemma].filter(Boolean),
    parts_of_speech: item.parts_of_speech || item.pos_values || (item.part_of_speech ? [item.part_of_speech] : []),
    sense_count: Number(item.sense_count ?? item.senses ?? 0),
    image_count: Number(item.image_count ?? item.images ?? 0),
  }
}

function normalizeWord(payload) {
  if (!payload) return PREVIEW_WORD
  if (payload.pos_groups) return payload
  const groups = payload.groups || payload.parts_of_speech || []
  return {
    ...payload,
    lemma: payload.lemma || payload.lemmatized_word || payload.word,
    observed_forms: payload.observed_forms || payload.forms || [],
    pos_groups: groups.map((group) => ({
      pos: group.pos || group.part_of_speech || group.name,
      senses: (group.senses || []).map((sense) => ({
        ...sense,
        id: sense.id || sense.sense_id || sense.source_sense_id,
        definition: sense.definition || sense.sense_oxford || sense.sense_wordnet || 'No definition available',
        image_count: Number(sense.image_count ?? sense.images ?? 0),
      })),
    })),
  }
}

function previewWordForLemma(lemma) {
  if (lemma?.lemma === PREVIEW_WORD.lemma) return PREVIEW_WORD
  const pos = lemma?.parts_of_speech?.[0] || 'noun'
  return {
    lemma: lemma?.lemma || '',
    observed_forms: lemma?.forms || [lemma?.lemma].filter(Boolean),
    pos_groups: [{
      pos,
      senses: [{ id: `${lemma?.lemma || 'word'}-preview`, definition: 'No live sense definition is available in the local preview.', image_count: 0 }],
    }],
  }
}

function normalizeImage(item) {
  const imageUrl = item.image_url || item.url || item.preview_url || ''
  const originalUrl = item.original_url || item.download_url || imageUrl
  return {
    ...item,
    id: item.id || item.path || item.filename,
    age: item.age || item.age_group || '',
    gender: item.gender || '',
    skin_tone: item.skin_tone || item.skin_color || '',
    background: item.background || item.background_style || 'regular',
    image_url: imageUrl.startsWith('/') ? buildApiUrl(imageUrl) : imageUrl,
    original_url: originalUrl.startsWith('/') ? buildApiUrl(originalUrl) : originalUrl,
    prompt: item.prompt || item.prompt_text || '',
    filename: item.filename || item.file_name || 'library-image.jpg',
  }
}

function matchesFilter(image, key, value) {
  if (!value || value.startsWith('All ')) return true
  return String(image[key] || '').toLowerCase() === value.toLowerCase()
}

function FilterSelect({ label, value, onChange, options, compact = false }) {
  return (
    <label className={compact ? 'library-filter library-filter-compact' : 'library-filter'}>
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} aria-label={label}>
        {options.map((option) => <option key={option} value={option}>{formatLabel(option)}</option>)}
      </select>
      <Icon name="chevron" size={16} />
    </label>
  )
}

function LemmaSearch({ query, setQuery, pos, setPos }) {
  return (
    <div className="library-search-row">
      <label className="library-search-field">
        <span className="sr-only">Search by lemma</span>
        <Icon name="search" size={24} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by lemma" autoComplete="off" />
      </label>
      <FilterSelect label="Parts of speech" value={pos} onChange={setPos} options={POS_OPTIONS} />
    </div>
  )
}

function LemmaList({ lemmas, selectedLemma, onSelect, loading }) {
  return (
    <aside className="library-lemma-pane" aria-label="Lemma results">
      <div className="library-pane-heading">
        <h2>Lemmas</h2>
        <span>{loading ? '…' : `${lemmas.length} results`}</span>
      </div>
      <div className="library-lemma-list" role="list">
        {loading ? (
          <div className="library-list-state"><span className="library-spinner" /> Searching lemmas…</div>
        ) : lemmas.length ? lemmas.map((lemma) => (
          <button
            type="button"
            role="listitem"
            key={lemma.lemma}
            className={`library-lemma-item${selectedLemma?.lemma === lemma.lemma ? ' is-selected' : ''}`}
            onClick={() => onSelect(lemma)}
          >
            <strong>{lemma.lemma}</strong>
            <span>{lemma.parts_of_speech.length ? lemma.parts_of_speech.join(', ') : 'word'} · {lemma.sense_count || 0} {lemma.sense_count === 1 ? 'sense' : 'senses'}{lemma.image_count ? ` · ${lemma.image_count} images` : ''}</span>
          </button>
        )) : (
          <div className="library-list-state library-list-empty"><Icon name="search" size={20} /><span>No lemmas match this search.</span></div>
        )}
      </div>
      <div className="library-pagination" aria-label="Lemma pagination">
        <button type="button" disabled aria-label="Previous page"><Icon name="arrowLeft" size={18} /></button>
        <button type="button" className="is-current" aria-current="page">1</button>
        <button type="button" disabled aria-label="Next page"><Icon name="arrowRight" size={18} /></button>
        <span>1 of 1</span>
      </div>
    </aside>
  )
}

function SenseRow({ sense, selected, onSelect }) {
  return (
    <button type="button" className={`library-sense-row${selected ? ' is-selected' : ''}`} onClick={() => onSelect(sense)}>
      <span className="library-sense-number">{sense.number || ''}</span>
      <span className="library-sense-copy">
        <strong>{sense.definition}</strong>
        <span><code>{sense.id || 'No sense ID'}</code><span className="library-sense-dot">•</span>{sense.image_count ? `${sense.image_count} V1 images` : 'No images'}</span>
      </span>
      <Icon name="arrowRight" size={20} />
    </button>
  )
}

function ImageCard({ image, selected, onSelect, onOpenLightbox }) {
  const hasImage = Boolean(image.image_url)
  return (
    <article className={`library-image-card${selected ? ' is-selected' : ''}`}>
      <button
        type="button"
        className={`library-image-button${hasImage ? '' : ' is-unavailable'}`}
        onClick={() => hasImage && onOpenLightbox(image)}
        aria-label={hasImage ? `Open ${formatLabel(image.background)} image` : `${formatLabel(image.background)} image unavailable`}
        aria-disabled={!hasImage}
      >
        {hasImage ? <img src={image.image_url} alt={`${formatLabel(image.background)} image for ${image.age} ${image.gender} ${image.skin_tone}`} /> : <span className="library-image-empty"><Icon name="image" size={28} /><span>Image unavailable</span></span>}
        <span className="library-image-check" aria-hidden="true">{selected ? '✓' : ''}</span>
      </button>
      <div className="library-image-copy">
        <div className="library-image-meta-line"><strong>{formatLabel(image.background)} background</strong><span>{formatLabel(image.age)} · {formatLabel(image.gender)} · {formatLabel(image.skin_tone)}</span></div>
        <span className="library-prompt-label">Prompt</span>
        <p>{image.prompt || 'No prompt stored for this image.'}</p>
        <button type="button" className="library-link-button" onClick={() => onSelect(image)}>View full prompt <Icon name="arrowRight" size={16} /></button>
      </div>
    </article>
  )
}

function PromptPanel({ image, onCopy, onOpenLightbox }) {
  if (!image) {
    return <aside className="library-prompt-panel library-prompt-panel-empty"><Icon name="image" size={28} /><h3>Image prompt</h3><p>Select an image to inspect its exact prompt.</p></aside>
  }
  return (
    <aside className="library-prompt-panel" aria-label="Image prompt">
      <h3>Image prompt</h3>
      <span className="library-prompt-label">Full prompt</span>
      <p className="library-full-prompt">{image.prompt || 'No prompt stored for this image.'}</p>
      <button type="button" className="library-primary-button" onClick={() => onCopy(image.prompt)}><Icon name="copy" size={18} /> Copy prompt</button>
      <button type="button" className="library-secondary-button" onClick={() => image.image_url && onOpenLightbox(image)} disabled={!image.image_url}><Icon name="external" size={18} /> Open full image</button>
    </aside>
  )
}

function ImageLightbox({ images, image, onClose, onChange, onCopy }) {
  const closeRef = useRef(null)
  const index = Math.max(0, images.findIndex((item) => item.id === image.id))
  useEffect(() => {
    closeRef.current?.focus()
    const handler = (event) => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'ArrowLeft' && index > 0) onChange(images[index - 1])
      if (event.key === 'ArrowRight' && index < images.length - 1) onChange(images[index + 1])
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [image.id, index, images, onChange, onClose])

  return (
    <div className="library-lightbox-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="library-lightbox" role="dialog" aria-modal="true" aria-label="Full-size image viewer">
        <div className="library-lightbox-heading">
          <div><h2>{formatLabel(image.background)} background</h2><p>abandon · noun · {formatLabel(image.age)} · {formatLabel(image.gender)} · {formatLabel(image.skin_tone)}</p></div>
          <button ref={closeRef} type="button" className="library-icon-button" onClick={onClose} aria-label="Close image viewer"><Icon name="close" size={22} /></button>
        </div>
        <div className="library-lightbox-body">
          <div className="library-lightbox-viewer">
            {image.image_url ? <img src={image.image_url} alt="Full-size library image" /> : <div className="library-image-empty"><Icon name="image" size={36} /><span>Original image unavailable</span></div>}
            <button type="button" className="library-lightbox-arrow left" onClick={() => index > 0 && onChange(images[index - 1])} disabled={index === 0} aria-label="Previous image"><Icon name="arrowLeft" size={22} /></button>
            <button type="button" className="library-lightbox-arrow right" onClick={() => index < images.length - 1 && onChange(images[index + 1])} disabled={index === images.length - 1} aria-label="Next image"><Icon name="arrowRight" size={22} /></button>
          </div>
          <aside className="library-lightbox-details">
            <h3>Image details</h3><span className="library-prompt-label">Prompt</span>
            <p className="library-full-prompt">{image.prompt || 'No prompt stored for this image.'}</p>
            <button type="button" className="library-primary-button" onClick={() => image.original_url && downloadImage(image)} disabled={!image.original_url}><Icon name="download" size={18} /> Download image</button>
            <button type="button" className="library-secondary-button" onClick={() => image.original_url && window.open(image.original_url, '_blank', 'noopener,noreferrer')} disabled={!image.original_url}><Icon name="external" size={18} /> Open original</button>
            <button type="button" className="library-text-button" onClick={() => onCopy(image.prompt)}><Icon name="copy" size={18} /> Copy prompt</button>
            <div className="library-file-meta"><span>{image.filename}</span><span>Full resolution</span></div>
          </aside>
        </div>
      </section>
    </div>
  )
}

function downloadImage(image) {
  const link = document.createElement('a')
  link.href = image.original_url
  link.download = image.filename || 'library-image.jpg'
  link.target = '_blank'
  link.rel = 'noreferrer'
  document.body.appendChild(link)
  link.click()
  link.remove()
}

export default function LibraryPage() {
  const [query, setQuery] = useState('abandon')
  const [pos, setPos] = useState('All parts of speech')
  const [lemmas, setLemmas] = useState(PREVIEW_LEMMAS)
  const [selectedLemma, setSelectedLemma] = useState(PREVIEW_LEMMAS[0])
  const [word, setWord] = useState(PREVIEW_WORD)
  const [activePos, setActivePos] = useState('noun')
  const [selectedSense, setSelectedSense] = useState(PREVIEW_WORD.pos_groups[0].senses[0])
  const [images, setImages] = useState(PREVIEW_IMAGES)
  const [selectedImage, setSelectedImage] = useState(PREVIEW_IMAGES[0])
  const [lightboxImage, setLightboxImage] = useState(null)
  const [loadingLemmas, setLoadingLemmas] = useState(false)
  const [loadingWord, setLoadingWord] = useState(false)
  const [loadingImages, setLoadingImages] = useState(false)
  const [imageError, setImageError] = useState('')
  const [copyMessage, setCopyMessage] = useState('')
  const [filters, setFilters] = useState({ age: 'All ages', gender: 'All genders', skin_tone: 'All skin tones', background: 'All backgrounds' })

  useEffect(() => {
    const timer = window.setTimeout(async () => {
      setLoadingLemmas(true)
      try {
        const response = await listLibraryLemmas({ q: query, pos: pos.startsWith('All ') ? '' : pos, limit: 20 })
        const next = (response.lemmas || response.results || []).map(normalizeLemma)
        setLemmas(next)
        setSelectedLemma((current) => next.find((item) => item.lemma === current?.lemma) || next[0] || null)
      } catch {
        if (query.trim().toLowerCase() === 'abandon') {
          setLemmas(PREVIEW_LEMMAS)
          setSelectedLemma((current) => current || PREVIEW_LEMMAS[0])
        } else {
          setLemmas([])
          setSelectedLemma(null)
        }
      } finally {
        setLoadingLemmas(false)
      }
    }, 280)
    return () => window.clearTimeout(timer)
  }, [query, pos])

  useEffect(() => {
    if (!selectedLemma) return undefined
    let cancelled = false
    setLoadingWord(true)
    getLibraryLemma(selectedLemma.lemma)
      .then((response) => {
        if (cancelled) return
        const next = normalizeWord(response)
        setWord(next)
        const nextPos = next.pos_groups?.[0]?.pos || 'noun'
        setActivePos(nextPos)
        const nextSense = next.pos_groups?.[0]?.senses?.[0] || null
        setSelectedSense(nextSense)
      })
      .catch(() => {
        if (!cancelled) {
          const fallback = previewWordForLemma(selectedLemma)
          setWord(fallback)
          setActivePos(fallback.pos_groups[0]?.pos || 'noun')
          setSelectedSense(fallback.pos_groups[0]?.senses?.[0] || null)
        }
      })
      .finally(() => !cancelled && setLoadingWord(false))
    return () => { cancelled = true }
  }, [selectedLemma])

  useEffect(() => {
    if (!selectedSense?.id) return undefined
    let cancelled = false
    setLoadingImages(true)
    setImageError('')
    listSenseImages(selectedSense.id)
      .then((response) => {
        if (cancelled) return
        const next = (response.images || response.results || []).map(normalizeImage)
        setImages(next)
        setSelectedImage(next[0] || null)
      })
      .catch(() => {
        if (!cancelled) {
          setImages(selectedSense.id === PREVIEW_WORD.pos_groups[0].senses[0].id ? PREVIEW_IMAGES : [])
          setSelectedImage(selectedSense.id === PREVIEW_WORD.pos_groups[0].senses[0].id ? PREVIEW_IMAGES[0] : null)
          setImageError('Failed to load images. Try again.')
        }
      })
      .finally(() => !cancelled && setLoadingImages(false))
    return () => { cancelled = true }
  }, [selectedSense])

  const activeGroup = useMemo(() => word?.pos_groups?.find((group) => group.pos === activePos) || word?.pos_groups?.[0], [activePos, word])
  const filteredImages = useMemo(() => images.filter((image) => matchesFilter(image, 'age', filters.age) && matchesFilter(image, 'gender', filters.gender) && matchesFilter(image, 'skin_tone', filters.skin_tone) && matchesFilter(image, 'background', filters.background)), [filters, images])

  function selectLemma(lemma) {
    setSelectedLemma(lemma)
    setSelectedImage(null)
    setFilters({ age: 'All ages', gender: 'All genders', skin_tone: 'All skin tones', background: 'All backgrounds' })
  }

  function selectSense(sense) {
    setSelectedSense(sense)
    setSelectedImage(null)
  }

  async function copyPrompt(prompt) {
    if (!prompt) return
    try { await navigator.clipboard.writeText(prompt) } catch { /* clipboard may be unavailable in local preview */ }
    setCopyMessage('Prompt copied')
    window.setTimeout(() => setCopyMessage(''), 1500)
  }

  return (
    <section className="library-page">
      <header className="library-page-heading"><h1>Word Library</h1><p>Search by lemma, explore senses, and inspect every V1 image prompt.</p></header>
      <LemmaSearch query={query} setQuery={setQuery} pos={pos} setPos={setPos} />
      <div className="library-mobile-back"><button type="button" onClick={() => setSelectedLemma(null)}><Icon name="arrowLeft" size={20} /> All lemmas</button></div>
      {!selectedLemma ? <div className="library-no-selection-list"><LemmaList lemmas={lemmas} selectedLemma={selectedLemma} onSelect={selectLemma} loading={loadingLemmas} /></div> : null}
      {selectedLemma ? (
        <div className="library-workspace">
          <LemmaList lemmas={lemmas} selectedLemma={selectedLemma} onSelect={selectLemma} loading={loadingLemmas} />
          <section className="library-detail-pane" aria-live="polite">
            <div className="library-word-heading"><div><h2>{word.lemma || selectedLemma.lemma}</h2><p>Lemma: {word.lemma || selectedLemma.lemma}</p></div><span className="library-word-forms">{(word.observed_forms || selectedLemma.forms || []).join(' · ')}</span></div>
            {loadingWord ? <div className="library-inline-loading"><span className="library-spinner" /> Loading word details…</div> : null}
            <div className="library-pos-tabs" role="tablist" aria-label="Parts of speech">
              {(word.pos_groups || []).map((group) => <button type="button" role="tab" aria-selected={activeGroup?.pos === group.pos} key={group.pos} className={activeGroup?.pos === group.pos ? 'is-active' : ''} onClick={() => { setActivePos(group.pos); selectSense(group.senses?.[0]) }}>{formatLabel(group.pos)} ({group.senses?.length || 0})</button>)}
            </div>
            <div className="library-sense-list">
              {(activeGroup?.senses || []).map((sense, index) => <SenseRow key={sense.id || index} sense={{ ...sense, number: index + 1 }} selected={selectedSense?.id === sense.id} onSelect={selectSense} />)}
            </div>
            <div className="library-gallery-heading"><div><h3>V1 images</h3><p>{selectedSense?.definition || 'Choose a sense to browse current inventory images.'}</p></div><span>{filteredImages.length} shown</span></div>
            <div className="library-filter-row">
              <FilterSelect label="Age" compact value={filters.age} onChange={(value) => setFilters((current) => ({ ...current, age: value }))} options={AGE_OPTIONS} />
              <FilterSelect label="Gender" compact value={filters.gender} onChange={(value) => setFilters((current) => ({ ...current, gender: value }))} options={GENDER_OPTIONS} />
              <FilterSelect label="Skin tone" compact value={filters.skin_tone} onChange={(value) => setFilters((current) => ({ ...current, skin_tone: value }))} options={SKIN_OPTIONS} />
              <FilterSelect label="Background" compact value={filters.background} onChange={(value) => setFilters((current) => ({ ...current, background: value }))} options={BACKGROUND_OPTIONS} />
            </div>
            <div className="library-gallery-layout">
              <div className="library-gallery-grid">
                {loadingImages ? <div className="library-gallery-state"><span className="library-spinner" /> Loading images…</div> : filteredImages.length ? filteredImages.map((image) => <ImageCard key={image.id} image={image} selected={selectedImage?.id === image.id} onSelect={setSelectedImage} onOpenLightbox={setLightboxImage} />) : <div className="library-gallery-state library-gallery-empty"><Icon name="image" size={28} /><strong>No images for this filter</strong><span>Try a different profile combination.</span></div>}
              </div>
              <PromptPanel image={selectedImage} onCopy={copyPrompt} onOpenLightbox={setLightboxImage} />
            </div>
            {imageError ? <div className="library-status-row is-error"><Icon name="warning" size={18} /><strong>Error</strong><span>{imageError}</span></div> : null}
          </section>
        </div>
      ) : null}
      {copyMessage ? <div className="library-toast" role="status">{copyMessage}</div> : null}
      {lightboxImage ? <ImageLightbox images={filteredImages.length ? filteredImages : images} image={lightboxImage} onClose={() => setLightboxImage(null)} onChange={setLightboxImage} onCopy={copyPrompt} /> : null}
    </section>
  )
}
