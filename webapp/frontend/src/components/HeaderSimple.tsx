import { Link, useLocation } from 'react-router-dom'
import type { NetworkMode } from '../types'

interface Props {
  networkMode: NetworkMode
  onToggleNetwork: () => void
}

export default function HeaderSimple({ networkMode, onToggleNetwork }: Props) {
  const location = useLocation()
  const isActive = (path: string) => location.pathname === path

  return (
    <header style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '16px 24px', borderBottom: '1px solid #1a1a2e',
      background: '#0f0f1a',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 32 }}>
        <Link to="/" style={{ color: '#fff', textDecoration: 'none', fontSize: 20, fontWeight: 700 }}>
          Miximus
        </Link>
        <nav style={{ display: 'flex', gap: 16 }}>
          {[
            { path: '/', label: 'Mixer' },
            { path: '/pools', label: 'Pools' },
            { path: '/status', label: 'Track' },
          ].map(({ path, label }) => (
            <Link key={path} to={path} style={{
              color: isActive(path) ? '#6c5ce7' : '#888',
              textDecoration: 'none', fontSize: 14, fontWeight: 500,
              padding: '4px 8px', borderRadius: 6,
              background: isActive(path) ? 'rgba(108,92,231,0.1)' : 'transparent',
            }}>
              {label}
            </Link>
          ))}
        </nav>
      </div>
      <button onClick={onToggleNetwork} style={{
        background: networkMode === 'testnet' ? 'rgba(255,193,7,0.15)' : 'rgba(76,175,80,0.15)',
        color: networkMode === 'testnet' ? '#ffc107' : '#4caf50',
        border: `1px solid ${networkMode === 'testnet' ? '#ffc107' : '#4caf50'}`,
        borderRadius: 20, padding: '6px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600,
      }}>
        {networkMode === 'testnet' ? 'TESTNET' : 'MAINNET'}
      </button>
    </header>
  )
}
