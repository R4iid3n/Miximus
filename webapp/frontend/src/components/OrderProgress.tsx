import type { OrderStep } from '../types'

interface Props {
  steps: OrderStep[]
  explorerUrl?: string
}

export default function OrderProgress({ steps, explorerUrl }: Props) {
  const statusColor = (s: string) => {
    switch (s) {
      case 'completed': return '#4caf50'
      case 'in_progress': return '#ffc107'
      case 'failed': return '#f44336'
      default: return '#555'
    }
  }

  const statusIcon = (s: string) => {
    switch (s) {
      case 'completed': return '\u2713'
      case 'in_progress': return '\u25CF'
      case 'failed': return '\u2717'
      default: return '\u25CB'
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {steps.map((step, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{
            width: 28, height: 28, borderRadius: '50%', display: 'flex',
            alignItems: 'center', justifyContent: 'center', fontSize: 14,
            background: `${statusColor(step.status)}22`,
            color: statusColor(step.status), fontWeight: 700,
            border: `2px solid ${statusColor(step.status)}`,
          }}>
            {statusIcon(step.status)}
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ color: '#fff', fontSize: 14, fontWeight: 500 }}>
              {step.name}
              {step.status === 'in_progress' && <span style={{ color: '#ffc107', marginLeft: 8, fontSize: 12 }}>Обработка...</span>}
            </div>
            {step.tx_hash && explorerUrl && (
              <a href={`${explorerUrl}/tx/${step.tx_hash}`} target="_blank" rel="noopener noreferrer"
                style={{ color: '#6c5ce7', fontSize: 12, textDecoration: 'none' }}>
                {step.tx_hash.slice(0, 16)}...
              </a>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
