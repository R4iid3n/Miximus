import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { useMixOrder } from '../hooks/useMixOrder'
import OrderProgress from '../components/OrderProgress'
import PrivacyAnalysis from '../components/PrivacyAnalysis'

export default function StatusPage() {
  const { orderId: urlOrderId } = useParams<{ orderId: string }>()
  const { order, loading, error, loadOrder } = useMixOrder()
  const [orderId, setOrderId] = useState(urlOrderId || '')

  useEffect(() => {
    if (urlOrderId) loadOrder(urlOrderId)
  }, [urlOrderId, loadOrder])

  const handleLookup = () => {
    if (orderId.trim()) loadOrder(orderId.trim())
  }

  return (
    <div style={{ maxWidth: 640, margin: '0 auto', padding: 24 }}>
      <h1 style={{ color: '#fff', fontSize: 24, marginBottom: 8 }}>Отследить заказ</h1>
      <p style={{ color: '#888', fontSize: 14, marginBottom: 24 }}>Введите ID заказа для проверки статуса миксинга.</p>

      <div style={{ display: 'flex', gap: 8, marginBottom: 24 }}>
        <input value={orderId} onChange={(e) => setOrderId(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleLookup()}
          placeholder="Введите ID заказа..." style={{
            flex: 1, padding: '12px 16px', background: '#1a1a2e', border: '1px solid #2a2a3e',
            borderRadius: 8, color: '#fff', fontSize: 14, outline: 'none',
          }} />
        <button onClick={handleLookup} disabled={loading || !orderId.trim()} style={{
          padding: '12px 24px', background: '#6c5ce7', color: '#fff', border: 'none',
          borderRadius: 8, cursor: 'pointer', fontSize: 14, fontWeight: 600,
        }}>
          {loading ? '...' : 'Найти'}
        </button>
      </div>

      {error && (
        <div style={{ background: 'rgba(244,67,54,0.1)', border: '1px solid #f44336', borderRadius: 8, padding: 12, marginBottom: 16, color: '#f44336', fontSize: 14 }}>
          {error}
        </div>
      )}

      {order && (
        <div style={{ background: '#1a1a2e', borderRadius: 12, padding: 20, border: '1px solid #2a2a3e' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
            <div>
              <div style={{ color: '#888', fontSize: 12 }}>Актив</div>
              <div style={{ color: '#fff', fontSize: 16, fontWeight: 700 }}>{order.symbol} ({order.chain})</div>
            </div>
            <div>
              <div style={{ color: '#888', fontSize: 12 }}>Статус</div>
              <div style={{
                color: order.status === 'completed' ? '#4caf50' : order.status === 'failed' ? '#f44336' : '#ffc107',
                fontSize: 16, fontWeight: 700,
              }}>{order.status.replace(/_/g, ' ').toUpperCase()}</div>
            </div>
            <div>
              <div style={{ color: '#888', fontSize: 12 }}>Получатель</div>
              <div style={{ color: '#fff', fontSize: 13 }}>{order.recipient_address}</div>
            </div>
            <div>
              <div style={{ color: '#888', fontSize: 12 }}>Выплата</div>
              <div style={{ color: '#00d2ff', fontSize: 14, fontWeight: 600 }}>{order.payout_display || order.payout_amount}</div>
            </div>
            {order.units > 1 && (
              <>
                <div>
                  <div style={{ color: '#888', fontSize: 12 }}>Единицы</div>
                  <div style={{ color: '#fff', fontSize: 14, fontWeight: 600 }}>{order.units} x {order.denomination_display}</div>
                </div>
                <div>
                  <div style={{ color: '#888', fontSize: 12 }}>Обработано</div>
                  <div style={{ color: '#00d2ff', fontSize: 14, fontWeight: 600 }}>{order.completed_units} / {order.units}</div>
                </div>
              </>
            )}
          </div>
          <OrderProgress steps={order.steps || []} />
        </div>
      )}

      {order && order.status === 'completed' && (
        <div style={{ marginTop: 16 }}>
          <PrivacyAnalysis orderId={order.order_id} />
        </div>
      )}
    </div>
  )
}
