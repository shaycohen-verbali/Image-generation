import React, { useEffect, useState } from 'react'
import AlgorithmStaticMap from '../components/AlgorithmStaticMap'
import { getConfig } from '../lib/api'

export default function AlgorithmPage() {
  const [assistantName, setAssistantName] = useState('')
  const [config, setConfig] = useState(null)
  const [diagramMode, setDiagramMode] = useState('csv_dag')

  useEffect(() => {
    let mounted = true
    const loadConfig = async () => {
      try {
        const config = await getConfig()
        if (mounted) {
          setConfig(config)
        }
        if (mounted && config?.openai_assistant_name) {
          setAssistantName(config.openai_assistant_name)
        }
      } catch (_error) {
        // Keep fallback.
      }
    }
    loadConfig()
    return () => {
      mounted = false
    }
  }, [])

  return (
    <section className="runs-page-stack">
      <div className="tab-row">
        <button
          type="button"
          className={diagramMode === 'csv_dag' ? 'tab active' : 'tab'}
          onClick={() => setDiagramMode('csv_dag')}
        >
          Parallel CSV DAG
        </button>
        <button
          type="button"
          className={diagramMode === 'legacy' ? 'tab active' : 'tab'}
          onClick={() => setDiagramMode('legacy')}
        >
          Legacy fallback runs
        </button>
      </div>
      <AlgorithmStaticMap assistantName={assistantName} config={config} mode={diagramMode} />
    </section>
  )
}
