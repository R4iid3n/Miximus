import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useNetworkMode } from './hooks/useNetworkMode'
import HeaderSimple from './components/HeaderSimple'
import MixPage from './pages/MixPage'
import PoolsPage from './pages/PoolsPage'
import StatusPage from './pages/StatusPage'

export default function App() {
  const { networkMode, toggleNetworkMode } = useNetworkMode()

  return (
    <BrowserRouter>
      <div style={{ minHeight: '100vh', background: '#0a0a0f', color: '#e0e0e0', fontFamily: 'system-ui, sans-serif' }}>
        <HeaderSimple
          networkMode={networkMode}
          onToggleNetwork={toggleNetworkMode}
        />

        <main style={{ maxWidth: 1200, margin: '0 auto', padding: '24px 16px' }}>
          <Routes>
            <Route path="/" element={<MixPage networkMode={networkMode} />} />
            <Route path="/pools" element={<PoolsPage networkMode={networkMode} />} />
            <Route path="/status" element={<StatusPage />} />
            <Route path="/status/:orderId" element={<StatusPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
