import type { NetworkMode } from '../types'
import { usePools } from '../hooks/usePools'

interface Props { networkMode: NetworkMode }

export default function PoolsPage({ networkMode }: Props) {
  const { pools, loading, error, refresh } = usePools(networkMode)

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 style={{ color: '#fff', fontSize: 24, margin: 0 }}>Пулы миксера</h1>
        <button onClick={refresh} style={{
          background: '#1a1a2e', color: '#888', border: '1px solid #2a2a3e',
          borderRadius: 8, padding: '8px 16px', cursor: 'pointer', fontSize: 13,
        }}>Обновить</button>
      </div>

      {loading && <div style={{ color: '#888', textAlign: 'center', padding: 40 }}>Загрузка...</div>}
      {error && <div style={{ color: '#f44336', textAlign: 'center', padding: 20 }}>{error}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
        {pools.map((pool) => (
          <div key={`${pool.symbol}-${pool.chain}`} style={{
            background: '#1a1a2e', borderRadius: 12, padding: 20,
            border: pool.enabled ? '1px solid #2a2a3e' : '1px solid #f4433644',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div style={{ color: '#fff', fontSize: 20, fontWeight: 700 }}>{pool.symbol}</div>
              <div style={{
                background: pool.enabled ? 'rgba(76,175,80,0.15)' : 'rgba(244,67,54,0.15)',
                color: pool.enabled ? '#4caf50' : '#f44336',
                padding: '4px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600,
              }}>{pool.enabled ? 'Активен' : 'Отключён'}</div>
            </div>
            <div style={{ color: '#888', fontSize: 13, marginBottom: 12 }}>{pool.chain}</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div>
                <div style={{ color: '#666', fontSize: 11 }}>Единица</div>
                <div style={{ color: '#00d2ff', fontSize: 14, fontWeight: 600 }}>{pool.denomination_display}</div>
              </div>
              <div>
                <div style={{ color: '#666', fontSize: 11 }}>Комиссия</div>
                <div style={{ color: '#ffc107', fontSize: 14, fontWeight: 600 }}>{(pool.commission_rate * 100).toFixed(1)}%</div>
              </div>
              <div>
                <div style={{ color: '#666', fontSize: 11 }}>Выплата / Ед.</div>
                <div style={{ color: '#4caf50', fontSize: 14, fontWeight: 600 }}>{pool.payout_display}</div>
              </div>
              <div>
                <div style={{ color: '#666', fontSize: 11 }}>Доступные ед.</div>
                <div style={{
                  color: pool.available_units > 0 ? '#8bc34a' : '#f44336',
                  fontSize: 14, fontWeight: 600,
                }}>
                  {pool.available_units}
                </div>
              </div>
            </div>
            {pool.mixer_contract && (
              <div style={{ marginTop: 12, color: '#555', fontSize: 11, wordBreak: 'break-all' }}>
                Контракт: {pool.mixer_contract}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
