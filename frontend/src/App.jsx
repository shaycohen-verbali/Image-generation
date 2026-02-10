import { useState } from 'react'
import SubmitPage from './pages/SubmitPage'
import RunsPage from './pages/RunsPage'
import EntryDetailPage from './pages/EntryDetailPage'
import ExportsPage from './pages/ExportsPage'

const tabs = [
  { id: 'submit', label: 'Submit' },
  { id: 'runs', label: 'Runs' },
  { id: 'detail', label: 'Entry Detail' },
  { id: 'exports', label: 'Exports' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState('submit')

  return (
    <div className="app-shell">
      <header className="hero">
        <div>
          <h1>AAC Image Generator</h1>
          <p>Word -> Prompt -> Draft -> Upgrade -> White background -> Score</p>
        </div>
      </header>

      <nav className="tab-row">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={tab.id === activeTab ? 'tab active' : 'tab'}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <main className="page-wrap">
        {activeTab === 'submit' && <SubmitPage />}
        {activeTab === 'runs' && <RunsPage />}
        {activeTab === 'detail' && <EntryDetailPage />}
        {activeTab === 'exports' && <ExportsPage />}
      </main>
    </div>
  )
}
