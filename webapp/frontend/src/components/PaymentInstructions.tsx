import { useState } from 'react'

interface Props {
  serviceAddress: string
  amount: string
  symbol: string
  chain: string
  expiresAt: string
}

export default function PaymentInstructions({ serviceAddress, amount, symbol, chain, expiresAt }: Props) {
  const [copied, setCopied] = useState<string | null>(null)

  const copy = async (text: string, field: string) => {
    await navigator.clipboard.writeText(text)
    setCopied(field)
    setTimeout(() => setCopied(null), 2000)
  }

  const expiryDate = new Date(expiresAt)
  const now = new Date()
  const secondsLeft = Math.max(0, Math.floor((expiryDate.getTime() - now.getTime()) / 1000))
  const minutesLeft = Math.floor(secondsLeft / 60)
  const hoursLeft = Math.floor(secondsLeft / 3600)
  const expiryDisplay = hoursLeft >= 2
    ? `${hoursLeft} ч`
    : minutesLeft >= 1
      ? `${minutesLeft} мин`
      : 'менее минуты'

  return (
    <div style={{ background: '#1a1a2e', borderRadius: 12, padding: 20, border: '1px solid #2a2a3e' }}>
      <h3 style={{ color: '#ffc107', margin: '0 0 16px', fontSize: 16 }}>Отправьте оплату</h3>

      <div style={{ marginBottom: 16 }}>
        <div style={{ color: '#888', fontSize: 12, marginBottom: 4 }}>Сумма</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <code style={{ color: '#00d2ff', fontSize: 18, fontWeight: 700 }}>{amount} {symbol}</code>
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ color: '#888', fontSize: 12, marginBottom: 4 }}>Отправьте на адрес</div>
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
            {copied === 'addr' ? 'Скопировано!' : 'Копировать'}
          </button>
        </div>
      </div>

      <div style={{ color: '#f44336', fontSize: 13 }}>
        Истекает через {expiryDisplay}. Отправьте точную сумму.
      </div>

      {chain === 'bitcoin' && (
        <div style={{
          marginTop: 16, padding: 14,
          background: 'rgba(108,92,231,0.12)',
          border: '1px solid rgba(108,92,231,0.4)',
          borderRadius: 10,
        }}>
          <div style={{ color: '#a29bfe', fontSize: 13, fontWeight: 700, marginBottom: 6 }}>
            Якорь приватности zkSNARK
          </div>
          <div style={{ color: '#ccc', fontSize: 12, lineHeight: 1.6 }}>
            Для Bitcoin-ордеров сервис автоматически публикует <strong style={{ color: '#a29bfe' }}>zkSNARK-доказательство</strong> в
            отдельном смарт-контракте на Ethereum. Это создаёт криптографическое
            подтверждение вашей анонимности: нуллификатор фиксируется в блокчейне,
            но связь между входящим и исходящим BTC-адресами не раскрывается.
            <br /><br />
            Анализ приватности будет доступен после завершения транзакции.
          </div>
        </div>
      )}
    </div>
  )
}
