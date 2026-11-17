import { useState } from 'react'
import { BookOpen, BookMarked, Settings as SettingsIcon, Sparkles, Users } from 'lucide-react'
import WorldSelect from './pages/WorldSelect'
import GamePage from './pages/GamePage'
import CharactersPage from './pages/CharactersPage'
import PromptStudio from './pages/PromptStudio'
import SettingsPage from './pages/Settings'

export type Page = 'worlds' | 'game' | 'characters' | 'prompts' | 'settings'

export default function App() {
  const [page, setPage] = useState<Page>('worlds')
  const [worldId, setWorldId] = useState<string | null>(null)
  const [worldName, setWorldName] = useState('')

  const enterWorld = (id: string, name: string) => {
    setWorldId(id)
    setWorldName(name)
    setPage('game')
  }

  const navItems: { key: Page; label: string; icon: typeof BookOpen; needsWorld: boolean }[] = [
    { key: 'worlds', label: '世界', icon: BookMarked, needsWorld: false },
    { key: 'game', label: '故事', icon: BookOpen, needsWorld: true },
    { key: 'characters', label: '人物', icon: Users, needsWorld: true },
    { key: 'prompts', label: 'Prompt', icon: Sparkles, needsWorld: false },
    { key: 'settings', label: '设置', icon: SettingsIcon, needsWorld: false },
  ]

  return (
    <div className="app-shell">
      <header className="top-bar">
        <button className="brand" onClick={() => setPage('worlds')} aria-label="回到世界列表">
          <BookOpen size={19} strokeWidth={1.7} />
          <span>StoryVerse</span>
        </button>
        <nav className="top-nav" aria-label="主导航">
          {navItems.map(({ key, label, icon: Icon, needsWorld }) => {
            if (needsWorld && !worldId) return null
            return (
              <button
                key={key}
                className={`nav-item ${page === key ? 'active' : ''}`}
                onClick={() => setPage(key)}
              >
                <Icon size={15} strokeWidth={1.7} />
                <span>{label}</span>
              </button>
            )
          })}
        </nav>
      </header>

      <main className="page-area">
        {page === 'worlds' && <WorldSelect onEnter={enterWorld} />}
        {page === 'game' && worldId && (
          <GamePage worldId={worldId} worldName={worldName} onBack={() => setPage('worlds')} />
        )}
        {page === 'characters' && worldId && (
          <CharactersPage worldId={worldId} worldName={worldName} />
        )}
        {page === 'prompts' && <PromptStudio />}
        {page === 'settings' && <SettingsPage />}
      </main>
    </div>
  )
}
