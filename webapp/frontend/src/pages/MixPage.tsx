import { useState, useMemo, useEffect } from 'react'
import type { Pool, NetworkMode } from '../types'
import { usePools } from '../hooks/usePools'
import { useMixOrder } from '../hooks/useMixOrder'
import PaymentInstructions from '../components/PaymentInstructions'
import OrderProgress from '../components/OrderProgress'
import PrivacyAnalysis from '../components/PrivacyAnalysis'

interface Props { networkMode: NetworkMode }

const CURRENCY_NAMES: Record<string, string> = {
  BTC: 'Bitcoin', ETH: 'Ethereum', USDT: 'Tether USDT', USDC: 'USD Coin',
  BNB: 'BNB', MATIC: 'Polygon', TRX: 'Tron', SOL: 'Solana', AVAX: 'Avalanche',
}

const STATUS_LABELS: Record<string, string> = {
  pending: 'Ожидание',
  confirmed: 'Подтверждён',
  processing: 'Обработка',
  proving: 'Генерация доказательства',
  withdrawing: 'Вывод',
  completed: 'Завершён',
  failed: 'Ошибка',
  expired: 'Истёк',
}

const CHAIN_LABELS: Record<string, string> = {
  bitcoin: 'Сеть Bitcoin', ethereum: 'Ethereum', bsc: 'BNB Chain',
  polygon: 'Polygon', tron: 'Tron', avalanche: 'Avalanche',
  arbitrum: 'Arbitrum One', base: 'Base', optimism: 'Optimism',
}

async function fetchUsdRates(symbols: string[]): Promise<Record<string, number>> {
  const ID_MAP: Record<string, string> = {
    BTC: 'bitcoin', ETH: 'ethereum', USDT: 'tether', USDC: 'usd-coin',
    BNB: 'binancecoin', MATIC: 'matic-network', TRX: 'tron', SOL: 'solana',
    AVAX: 'avalanche-2',
  }
  const ids = [...new Set(symbols.map(s => ID_MAP[s.toUpperCase()]).filter(Boolean))]
  if (!ids.length) return {}
  try {
    const res = await fetch(
      `https://api.coingecko.com/api/v3/simple/price?ids=${ids.join(',')}&vs_currencies=usd`,
      { signal: AbortSignal.timeout(6000) }
    )
    const data = await res.json()
    const out: Record<string, number> = {}
    for (const sym of symbols) {
      const id = ID_MAP[sym.toUpperCase()]
      if (id && data[id]?.usd) out[sym.toUpperCase()] = data[id].usd
    }
    return out
  } catch {
    return {}
  }
}

function formatAmount(n: number, symbol: string): string {
  const stables = ['USDT', 'USDC', 'DAI', 'BUSD']
  if (stables.includes(symbol.toUpperCase())) return n.toFixed(2)
  if (n >= 100) return n.toFixed(2)
  if (n >= 1) return n.toFixed(4)
  return n.toPrecision(5).replace(/\.?0+$/, '')
}

export default function MixPage({ networkMode }: Props) {
  const { pools, loading: poolsLoading } = usePools(networkMode)
  const { order, loading, error, create, submitTx, startPolling, reset } = useMixOrder()
  const [selectedPool, setSelectedPool] = useState<Pool | null>(null)
  const [recipient, setRecipient] = useState('')
  const [amountInput, setAmountInput] = useState('')
  const [txHash, setTxHash] = useState('')
  const [step, setStep] = useState<'select' | 'pay' | 'track'>('select')
  const [usdRates, setUsdRates] = useState<Record<string, number>>({})

  useEffect(() => {
    if (!pools.length) return
    fetchUsdRates([...new Set(pools.map(p => p.symbol))]).then(setUsdRates)
  }, [pools])

  const denomValue = useMemo(() => {
    if (!selectedPool) return 0
    return parseFloat(selectedPool.denomination_display) || 0
  }, [selectedPool])

  const calc = useMemo(() => {
    const raw = parseFloat(amountInput)
    if (!raw || raw <= 0 || !denomValue || !selectedPool) return null
    const units = Math.max(1, Math.round(raw / denomValue))
    if (units > 100) return { units: 0, send: 0, fee: 0, receive: 0, tooLarge: true, rounded: false }
    const send = units * denomValue
    const fee = send * selectedPool.commission_rate
    const rounded = Math.abs(send - raw) > denomValue * 0.0001
    return { units, send, fee, receive: send - fee, tooLarge: false, rounded }
  }, [amountInput, denomValue, selectedPool])

  const usdRate = selectedPool ? (usdRates[selectedPool.symbol.toUpperCase()] ?? 0) : 0
  const usdHint = calc && !calc.tooLarge && usdRate
    ? `≈ $${(calc.send * usdRate).toLocaleString('en-US', { maximumFractionDigits: 0 })}`
    : null

  const presets = useMemo(() => {
    if (!denomValue) return []
    const maxAmount = denomValue * 100
    return [1, 5, 10, 25, 50, 100]
      .map(m => denomValue * m)
      .filter(v => v <= maxAmount)
      .map(v => ({ value: v, label: formatAmount(v, selectedPool?.symbol ?? '') }))
      .slice(0, 6)
  }, [denomValue, selectedPool])

  const handlePoolSelect = (pool: Pool) => {
    setSelectedPool(pool)
    setAmountInput('')
  }

  const handleCreate = async () => {
    if (!selectedPool || !recipient || !calc || calc.units < 1) return
    try {
      const result = await create(selectedPool.symbol, selectedPool.chain, recipient, networkMode, calc.units)
      if (result) setStep('pay')
    } catch { }
  }

  const handleSubmitTx = async () => {
    if (!txHash || !order) return
    const result = await submitTx(txHash)
    if (result) { startPolling(order.order_id); setStep('track') }
  }

  const handleReset = () => {
    reset(); setSelectedPool(null); setRecipient(''); setAmountInput(''); setTxHash(''); setStep('select')
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '13px 16px', background: '#1a1a2e',
    border: '1px solid #2a2a3e', borderRadius: 8, color: '#fff',
    fontSize: 15, outline: 'none', boxSizing: 'border-box',
  }

  const sectionLabel = (n: number, text: string) => (
    <div style={{ color: '#666', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>
      {n} · {text}
    </div>
  )

  return (
    <div style={{ maxWidth: 580, margin: '0 auto', padding: '24px 20px' }}>

      {/* Header */}
      <div style={{ marginBottom: 32 }}>
        <h1 style={{ color: '#fff', fontSize: 26, fontWeight: 700, margin: 0 }}>
          Криптовалютный миксер
        </h1>
        <p style={{ color: '#555', fontSize: 14, margin: '6px 0 0' }}>
          Анонимные переводы с использованием доказательств с нулевым разглашением. История транзакций скрыта.
        </p>
      </div>

      {error && (
        <div style={{
          background: 'rgba(244,67,54,0.1)', border: '1px solid rgba(244,67,54,0.4)',
          borderRadius: 8, padding: '12px 14px', marginBottom: 20, color: '#f44336', fontSize: 14,
        }}>
          {error}
        </div>
      )}

      {step === 'select' && (
        <>
          {/* ── Step 1: choose currency ── */}
          <div style={{ marginBottom: 28 }}>
            {sectionLabel(1, 'Выберите валюту')}
            {poolsLoading ? (
              <div style={{ color: '#555', padding: '20px 0' }}>Загрузка пулов…</div>
            ) : pools.length === 0 ? (
              <div style={{ color: '#555', padding: '20px 0' }}>Нет доступных пулов.</div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 10 }}>
                {pools.map(pool => {
                  const isSelected = selectedPool?.symbol === pool.symbol && selectedPool?.chain === pool.chain
                  const available = pool.available_units > 0 || pool.mixer_contract === 'custodial'
                  const rate = usdRates[pool.symbol.toUpperCase()]
                  const denom = parseFloat(pool.denomination_display) || 0

                  return (
                    <button
                      key={`${pool.symbol}-${pool.chain}`}
                      onClick={() => available && handlePoolSelect(pool)}
                      style={{
                        background: isSelected ? 'rgba(108,92,231,0.2)' : '#111120',
                        border: `2px solid ${isSelected ? '#6c5ce7' : available ? '#1e1e35' : '#181818'}`,
                        borderRadius: 12, padding: '14px 14px', cursor: available ? 'pointer' : 'not-allowed',
                        textAlign: 'left', transition: 'border-color 0.15s', opacity: available ? 1 : 0.45,
                      }}
                    >
                      <div style={{ color: '#fff', fontSize: 15, fontWeight: 700 }}>
                        {CURRENCY_NAMES[pool.symbol] || pool.symbol}
                      </div>
                      <div style={{ color: '#555', fontSize: 11, marginTop: 2, marginBottom: 10 }}>
                        {CHAIN_LABELS[pool.chain] || pool.chain}
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
                        <div style={{ color: '#888', fontSize: 12 }}>
                          Комиссия <strong style={{ color: '#ffc107' }}>{(pool.commission_rate * 100).toFixed(1)}%</strong>
                        </div>
                        {rate && denom > 0 && (
                          <div style={{ color: '#555', fontSize: 11 }}>
                            от ${(denom * rate).toFixed(2)}
                          </div>
                        )}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 8 }}>
                        <span style={{
                          width: 6, height: 6, borderRadius: '50%',
                          background: available ? '#4caf50' : '#f44336',
                          display: 'inline-block', flexShrink: 0,
                        }} />
                        <span style={{ color: available ? '#4caf50' : '#888', fontSize: 11 }}>
                          {available ? 'Доступно' : 'Недоступно'}
                        </span>
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {/* ── Step 2: amount ── */}
          {selectedPool && (
            <div style={{ marginBottom: 28 }}>
              {sectionLabel(2, `Сколько ${selectedPool.symbol} смешать?`)}

              {/* Big amount input */}
              <div style={{ position: 'relative' }}>
                <input
                  value={amountInput}
                  onChange={e => setAmountInput(e.target.value)}
                  placeholder="0.00"
                  type="number"
                  min={denomValue}
                  style={{
                    width: '100%', padding: '16px 70px 16px 18px',
                    background: '#111120', border: '1px solid #1e1e35',
                    borderRadius: 10, color: '#fff', fontSize: 22,
                    outline: 'none', boxSizing: 'border-box', fontWeight: 600,
                  }}
                />
                <span style={{
                  position: 'absolute', right: 18, top: '50%', transform: 'translateY(-50%)',
                  color: '#6c5ce7', fontWeight: 700, fontSize: 14, pointerEvents: 'none',
                }}>
                  {selectedPool.symbol}
                </span>
              </div>

              {usdHint && (
                <div style={{ color: '#555', fontSize: 13, marginTop: 5, paddingLeft: 2 }}>{usdHint}</div>
              )}

              {/* Quick amounts */}
              <div style={{ display: 'flex', gap: 7, marginTop: 10, flexWrap: 'wrap' }}>
                {presets.map(p => (
                  <button
                    key={p.value}
                    onClick={() => setAmountInput(String(p.value))}
                    style={{
                      background: parseFloat(amountInput) === p.value ? 'rgba(108,92,231,0.25)' : '#111120',
                      border: `1px solid ${parseFloat(amountInput) === p.value ? '#6c5ce7' : '#1e1e35'}`,
                      borderRadius: 6, padding: '5px 11px', cursor: 'pointer',
                      color: '#aaa', fontSize: 12, transition: 'all 0.15s',
                    }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              {/* Summary */}
              {calc && (
                <div style={{
                  marginTop: 14, borderRadius: 10,
                  border: calc.tooLarge ? '1px solid rgba(244,67,54,0.4)' : '1px solid #1e1e35',
                  overflow: 'hidden',
                }}>
                  {calc.tooLarge ? (
                    <div style={{ padding: '12px 16px', color: '#f44336', fontSize: 14, background: 'rgba(244,67,54,0.07)' }}>
                      Слишком большая сумма. Максимум: {formatAmount(denomValue * 100, selectedPool.symbol)} {selectedPool.symbol} за заказ.
                    </div>
                  ) : (
                    <>
                      <div style={{ display: 'flex', justifyContent: 'space-between', padding: '13px 16px', background: '#0c0c1a' }}>
                        <span style={{ color: '#888', fontSize: 14 }}>Вы отправляете</span>
                        <div style={{ textAlign: 'right' }}>
                          <span style={{ color: '#fff', fontSize: 15, fontWeight: 600 }}>
                            {formatAmount(calc.send, selectedPool.symbol)} {selectedPool.symbol}
                          </span>
                          {usdRate > 0 && (
                            <div style={{ color: '#555', fontSize: 11, marginTop: 1 }}>
                              ≈ ${(calc.send * usdRate).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                            </div>
                          )}
                        </div>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 16px', background: '#0c0c1a', borderTop: '1px solid #111' }}>
                        <span style={{ color: '#888', fontSize: 13 }}>
                          Комиссия сервиса ({(selectedPool.commission_rate * 100).toFixed(1)}%)
                        </span>
                        <span style={{ color: '#ffc107', fontSize: 13 }}>
                          −{formatAmount(calc.fee, selectedPool.symbol)} {selectedPool.symbol}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', padding: '14px 16px', background: '#0d0d1e', borderTop: '1px solid #111' }}>
                        <span style={{ color: '#ccc', fontSize: 15, fontWeight: 600 }}>Вы получите</span>
                        <div style={{ textAlign: 'right' }}>
                          <span style={{ color: '#4caf50', fontSize: 17, fontWeight: 700 }}>
                            {formatAmount(calc.receive, selectedPool.symbol)} {selectedPool.symbol}
                          </span>
                          {usdRate > 0 && (
                            <div style={{ color: '#3a8a3a', fontSize: 11, marginTop: 1 }}>
                              ≈ ${(calc.receive * usdRate).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                            </div>
                          )}
                        </div>
                      </div>
                      {calc.rounded && (
                        <div style={{ padding: '8px 16px', background: '#0a0a16', borderTop: '1px solid #111', color: '#555', fontSize: 11 }}>
                          ✦ Сумма округлена до ближайшего кратного значения ({formatAmount(calc.send, selectedPool.symbol)} {selectedPool.symbol})
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          )}

          {/* ── Step 3: recipient ── */}
          {calc && calc.units > 0 && !calc.tooLarge && (
            <div style={{ marginBottom: 8 }}>
              {sectionLabel(3, 'Адрес получателя')}
              <input
                value={recipient}
                onChange={e => setRecipient(e.target.value)}
                placeholder={
                  selectedPool?.chain === 'bitcoin' ? 'bc1q… or tb1q…' :
                  selectedPool?.chain === 'tron' ? 'T…' : '0x…'
                }
                style={inputStyle}
              />
              <p style={{ color: '#444', fontSize: 12, margin: '6px 0 16px' }}>
                Перемешанные средства будут отправлены на этот адрес. Проверьте его перед продолжением.
              </p>
              <button
                onClick={handleCreate}
                disabled={loading || !recipient.trim()}
                style={{
                  width: '100%', padding: '15px',
                  background: loading || !recipient.trim() ? '#1a1a2e' : '#6c5ce7',
                  color: loading || !recipient.trim() ? '#555' : '#fff',
                  border: 'none', borderRadius: 10, fontSize: 16, fontWeight: 700,
                  cursor: loading || !recipient.trim() ? 'default' : 'pointer',
                  transition: 'background 0.2s',
                }}
              >
                {loading
                  ? 'Создание заказа…'
                  : `Смешать ${formatAmount(calc.send, selectedPool?.symbol ?? '')} ${selectedPool?.symbol} →`}
              </button>
            </div>
          )}
        </>
      )}

      {/* ── Pay step ── */}
      {step === 'pay' && order && (
        <>
          <PaymentInstructions
            serviceAddress={order.service_address}
            amount={order.total_amount_display || order.denomination_display || order.denomination}
            symbol={order.symbol}
            chain={selectedPool?.chain ?? order.chain ?? ''}
            expiresAt={order.expires_at}
          />

          <div style={{ marginTop: 24 }}>
            <div style={{ color: '#666', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>
              Шаг 2 · Подтвердите оплату
            </div>
            <p style={{ color: '#888', fontSize: 13, marginBottom: 8 }}>
              После отправки вставьте ID вашей транзакции (TXID / хэш) ниже.
            </p>
            <input
              value={txHash}
              onChange={e => setTxHash(e.target.value)}
              placeholder="Хэш транзакции / TXID…"
              style={inputStyle}
            />
            <button
              onClick={handleSubmitTx}
              disabled={loading || !txHash}
              style={{
                marginTop: 12, width: '100%', padding: '14px',
                background: loading || !txHash ? '#1a1a2e' : '#4caf50',
                color: loading || !txHash ? '#555' : '#fff',
                border: 'none', borderRadius: 10, fontSize: 16, fontWeight: 700,
                cursor: loading || !txHash ? 'default' : 'pointer',
              }}
            >
              {loading ? 'Отправка…' : 'Я отправил платёж →'}
            </button>
          </div>
          <div style={{ textAlign: 'center', marginTop: 16 }}>
            <span style={{ color: '#444', fontSize: 12 }}>Заказ №: </span>
            <code style={{ color: '#6c5ce7', fontSize: 12 }}>{order.order_id}</code>
          </div>
        </>
      )}

      {/* ── Track step ── */}
      {step === 'track' && order && (
        <>
          <div style={{ background: '#111120', borderRadius: 12, padding: 20, border: '1px solid #1e1e35', marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
              <div>
                <div style={{ color: '#555', fontSize: 11 }}>Заказ №</div>
                <code style={{ color: '#6c5ce7', fontSize: 13 }}>{order.order_id}</code>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ color: '#555', fontSize: 11 }}>Статус</div>
                <div style={{
                  color: order.status === 'completed' ? '#4caf50' : order.status === 'failed' ? '#f44336' : '#ffc107',
                  fontSize: 14, fontWeight: 700,
                }}>
                  {STATUS_LABELS[order.status] ?? order.status.replace(/_/g, ' ')}
                </div>
              </div>
            </div>
            <OrderProgress steps={order.steps || []} />
          </div>

          {order.status === 'completed' && (
            <div style={{
              background: 'rgba(76,175,80,0.08)', border: '1px solid rgba(76,175,80,0.3)',
              borderRadius: 10, padding: 20, textAlign: 'center', marginBottom: 16,
            }}>
              <div style={{ color: '#4caf50', fontSize: 22, fontWeight: 700, marginBottom: 6 }}>✓ Микс завершён</div>
              <div style={{ color: '#aaa', fontSize: 14 }}>
                {order.payout_display || order.payout_amount} отправлено на {order.recipient_address.slice(0, 14)}…
              </div>
            </div>
          )}

          {order.status === 'completed' && (
            <div style={{ marginBottom: 16 }}>
              <PrivacyAnalysis orderId={order.order_id} />
            </div>
          )}

          <button onClick={handleReset} style={{
            width: '100%', padding: '12px', background: 'transparent',
            color: '#666', border: '1px solid #1e1e35', borderRadius: 8, cursor: 'pointer', fontSize: 14,
          }}>
            Начать новый микс
          </button>
        </>
      )}
    </div>
  )
}
