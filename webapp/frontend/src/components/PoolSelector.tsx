import type { Pool } from '../types'

interface Props {
  pools: Pool[]
  selected: Pool | null
  onSelect: (pool: Pool) => void
  loading: boolean
}

export default function PoolSelector({ pools, selected, onSelect, loading }: Props) {
  if (loading) return <div style={{ color: '#888', textAlign: 'center', padding: 40 }}>Загрузка пулов...</div>
  if (!pools.length) return <div style={{ color: '#888', textAlign: 'center', padding: 40 }}>Нет доступных пулов</div>

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
      {pools.map((pool) => {
        const isSelected = selected?.symbol === pool.symbol && selected?.chain === pool.chain
        return (
          <button key={`${pool.symbol}-${pool.chain}`} onClick={() => onSelect(pool)} style={{
            background: isSelected ? 'rgba(108,92,231,0.15)' : '#1a1a2e',
            border: `2px solid ${isSelected ? '#6c5ce7' : '#2a2a3e'}`,
            borderRadius: 12, padding: 16, cursor: 'pointer', textAlign: 'left',
            transition: 'all 0.2s',
          }}>
            <div style={{ color: '#fff', fontSize: 18, fontWeight: 700, marginBottom: 4 }}>{pool.symbol}</div>
            <div style={{ color: '#888', fontSize: 12, marginBottom: 8 }}>{pool.chain}</div>
            <div style={{ color: '#00d2ff', fontSize: 14, fontWeight: 600 }}>Единица: {pool.denomination_display}</div>
            <div style={{ color: '#4caf50', fontSize: 12, marginTop: 4 }}>
              Комиссия: {(pool.commission_rate * 100).toFixed(1)}% | За единицу: {pool.payout_display}
            </div>
            <div style={{
              color: pool.available_units > 0 ? '#8bc34a' : '#f44336',
              fontSize: 11, marginTop: 6, fontWeight: 600,
            }}>
              {pool.mixer_contract === 'custodial'
                ? `Кастодиальный · ${pool.available_units} ед.`
                : `В пуле: ${pool.available_units} ед.`}
            </div>
          </button>
        )
      })}
    </div>
  )
}
