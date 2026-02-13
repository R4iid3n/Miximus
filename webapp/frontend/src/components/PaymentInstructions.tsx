import { useState } from 'react'

interface Props {
  serviceAddress: string
  amount: string
  symbol: string
  expiresAt: string
}

export default function PaymentInstructions({ serviceAddress, amount, symbol, expiresAt }: Props) {
  const [copied, setCopied] = useState<string | null>(null)

  const copy = async (text: string, field: string) => {
    await navigator.clipboard.writeText(text)
    setCopied(field)
    setTimeout(() => setCopied(null), 2000)
  }

  const expiryDate = new Date(expiresAt)
  const now = new Date()
  const minutesLeft = Math.max(0, Math.floor((expiryDate.getTime() - now.getTime()) / 60000))

  return (
    <div style={{ background: '#1a1a2e', borderRadius: 12, padding: 20, border: '1px solid #2a2a3e' }}>
      <h3 style={{ color: '#ffc107', margin: '0 0 16px', fontSize: 16 }}>Send Payment</h3>

      <div style={{ marginBottom: 16 }}>
        <div style={{ color: '#888', fontSize: 12, marginBottom: 4 }}>Amount</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <code style={{ color: '#00d2ff', fontSize: 18, fontWeight: 700 }}>{amount} {symbol}</code>
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ color: '#888', fontSize: 12, marginBottom: 4 }}>Send to address</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <code style={{
            color: '#fff', fontSize: 13, background: '#0f0f1a', padding: '8px 12px',
            borderRadius: 8, flex: 1, wordBreak: 'break-all',
          }}>{serviceAddress}</code>
          <button onClick={() => copy(serviceAddress, 'addr')} style={{
            background: copied === 'addr' ? '#4caf50' : '#6c5ce7',
            color: '#fff', border: 'none', borderRadius: 8, padding: '8px 16px',
            cursor: 'pointer', fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap',
          }}>
            {copied === 'addr' ? 'Copied!' : 'Copy'}
          </button>
        </div>
      </div>

      <div style={{ color: '#f44336', fontSize: 13 }}>
        Expires in {minutesLeft} min. Send the exact amount.
      </div>
    </div>
  )
}
