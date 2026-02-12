import React, { useEffect, useState } from 'react'
import AlgorithmStaticMap from '../components/AlgorithmStaticMap'
import { getConfig } from '../lib/api'

export default function AlgorithmPage() {
  const [assistantName, setAssistantName] = useState('')

  useEffect(() => {
    let mounted = true
    const loadConfig = async () => {
      try {
        const config = await getConfig()
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
      <AlgorithmStaticMap assistantName={assistantName} />
    </section>
  )
}
