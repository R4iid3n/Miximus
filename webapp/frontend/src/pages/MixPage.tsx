import { useState, useMemo } from 'react'
import type { Pool, NetworkMode } from '../types'
import { usePools } from '../hooks/usePools'
import { useMixOrder } from '../hooks/useMixOrder'
import PoolSelector from '../components/PoolSelector'
import PaymentInstructions from '../components/PaymentInstructions'
import OrderProgress from '../components/OrderProgress'

interface Props { networkMode: NetworkMode }

/** Parse the human-readable denomination from "0.06 ETH" → 0.06 */
function parseDenominationValue(display: string): number {
  const num = parseFloat(display)
  return isNaN(num) ? 0 : num
}

export default function MixPage({ networkMode }: Props) {
  const { pools, loading: poolsLoading } = usePools(networkMode)
  const { order, loading, error, create, submitTx, startPolling, reset } = useMixOrder()
  const [selectedPool, setSelectedPool] = useState<Pool | null>(null)
  const [recipient, setRecipient] = useState('')
  const [amountInput, setAmountInput] = useState('')
  const [txHash, setTxHash] = useState('')
  const [step, setStep] = useState<'select' | 'pay' | 'track'>('select')

  // Calculate units from the amount input
  const denomValue = useMemo(() => {
    if (!selectedPool) return 0
    return parseDenominationValue(selectedPool.denomination_display)
  }, [selectedPool])

  const { units, totalAmount, isValid: amountValid } = useMemo(() => {
    const amount = parseFloat(amountInput)
    if (!amount || !denomValue || denomValue <= 0) {
      return { units: 0, totalAmount: 0, isValid: false }
    }
    const u = Math.round(amount / denomValue)
    if (u < 1 || u > 100) {
      return { units: u, totalAmount: amount, isValid: false }
    }
    // Check it's a clean multiple (within floating-point tolerance)
    const expectedAmount = u * denomValue
    const diff = Math.abs(amount - expectedAmount)
    const isClean = diff < denomValue * 0.001
    return { units: u, totalAmount: amount, isValid: isClean }
  }, [amountInput, denomValue])

  const handlePoolSelect = (pool: Pool | null) => {
    setSelectedPool(pool)
    setAmountInput('')
  }

  const handleCreate = async () => {
    if (!selectedPool || !recipient || !amountValid) return
    try {
      const result = await create(selectedPool.symbol, selectedPool.chain, recipient, networkMode, units)
      if (result) setStep('pay')
    } catch { /* error shown via hook */ }
  }

  const handleSubmitTx = async () => {
    if (!txHash || !order) return
    const result = await submitTx(txHash)
    if (result) {
      startPolling(order.order_id)
      setStep('track')
    }
  }

  const handleReset = () => {
    reset()
    setSelectedPool(null)
    setRecipient('')
    setAmountInput('')
    setTxHash('')
    setStep('select')
  }

  return (
    <div style={{ maxWidth: 640, margin: '0 auto', padding: 24 }}>
      <h1 style={{ color: '#fff', fontSize: 24, marginBottom: 8 }}>Cryptocurrency Mixer</h1>
      <p style={{ color: '#888', fontSize: 14, marginBottom: 24 }}>
        Select a pool, enter the amount (multiple of unit size) and recipient address.
      </p>

      {error && (
        <div style={{ background: 'rgba(244,67,54,0.1)', border: '1px solid #f44336', borderRadius: 8, padding: 12, marginBottom: 16, color: '#f44336', fontSize: 14 }}>
          {error}
        </div>
      )}

      {step === 'select' && (
        <>
          <h3 style={{ color: '#ccc', fontSize: 16, marginBottom: 12 }}>1. Select Pool</h3>
          <PoolSelector pools={pools} selected={selectedPool} onSelect={handlePoolSelect} loading={poolsLoading} />

          {selectedPool && (
            <div style={{ marginTop: 24 }}>
              <h3 style={{ color: '#ccc', fontSize: 16, marginBottom: 12 }}>2. Amount to Mix</h3>
              <p style={{ color: '#888', fontSize: 13, marginBottom: 8 }}>
                Minimum unit: {selectedPool.denomination_display}. Enter an amount that is a multiple of the unit size.
              </p>
              <input
                value={amountInput}
                onChange={(e) => setAmountInput(e.target.value)}
                placeholder={`e.g. ${denomValue}, ${(denomValue * 2).toString()}, ${(denomValue * 5).toString()}...`}
                type="number"
                step={denomValue || 'any'}
                min={denomValue || 0}
                style={{
                  width: '100%', padding: '12px 16px', background: '#1a1a2e', border: '1px solid #2a2a3e',
                  borderRadius: 8, color: '#fff', fontSize: 16, outline: 'none', boxSizing: 'border-box',
                }}
              />

              {/* Unit calculation display */}
              {amountInput && (
                <div style={{
                  marginTop: 12, padding: 12, background: '#0f0f1a', borderRadius: 8,
                  border: amountValid ? '1px solid #2a2a3e' : '1px solid #f4433666',
                }}>
                  {amountValid ? (
                    <>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                        <span style={{ color: '#888', fontSize: 13 }}>Mixer units:</span>
                        <span style={{ color: '#00d2ff', fontSize: 14, fontWeight: 700 }}>
                          {units} x {selectedPool.denomination_display}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                        <span style={{ color: '#888', fontSize: 13 }}>Fee ({(selectedPool.commission_rate * 100).toFixed(1)}%):</span>
                        <span style={{ color: '#ffc107', fontSize: 13 }}>
                          {(totalAmount * selectedPool.commission_rate).toFixed(6)} {selectedPool.symbol}
                        </span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: '#888', fontSize: 13 }}>You receive:</span>
                        <span style={{ color: '#4caf50', fontSize: 14, fontWeight: 700 }}>
                          {(totalAmount * (1 - selectedPool.commission_rate)).toFixed(6)} {selectedPool.symbol}
                        </span>
                      </div>
                      {selectedPool.mixer_contract !== 'custodial' && units > selectedPool.available_units && (
                        <div style={{ marginTop: 8, color: '#f44336', fontSize: 12 }}>
                          Insufficient liquidity: {selectedPool.available_units} units available, {units} requested.
                        </div>
                      )}
                    </>
                  ) : (
                    <div style={{ color: '#f44336', fontSize: 13 }}>
                      {units > 100
                        ? 'Maximum 100 units per order.'
                        : `Amount must be a multiple of ${selectedPool.denomination_display}`}
                    </div>
                  )}
                </div>
              )}

              {amountValid && (
                <div style={{ marginTop: 24 }}>
                  <h3 style={{ color: '#ccc', fontSize: 16, marginBottom: 12 }}>3. Recipient Address</h3>
                  <p style={{ color: '#888', fontSize: 13, marginBottom: 8 }}>
                    Mixed funds will be sent to this address.
                  </p>
                  <input value={recipient} onChange={(e) => setRecipient(e.target.value)}
                    placeholder="0x... / T... / m..." style={{
                      width: '100%', padding: '12px 16px', background: '#1a1a2e', border: '1px solid #2a2a3e',
                      borderRadius: 8, color: '#fff', fontSize: 14, outline: 'none', boxSizing: 'border-box',
                    }} />
                  <button onClick={handleCreate} disabled={loading || !recipient || (selectedPool.mixer_contract !== 'custodial' && units > selectedPool.available_units)}
                    style={{
                      marginTop: 16, width: '100%', padding: '14px', background: (loading || (selectedPool.mixer_contract !== 'custodial' && units > selectedPool.available_units)) ? '#555' : '#6c5ce7',
                      color: '#fff', border: 'none', borderRadius: 8, fontSize: 16, fontWeight: 700,
                      cursor: loading ? 'default' : 'pointer',
                    }}>
                    {loading ? 'Creating order...' : `Create Order (${units} units)`}
                  </button>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {step === 'pay' && order && (
        <>
          <PaymentInstructions
            serviceAddress={order.service_address}
            amount={order.total_amount_display || order.denomination_display || order.denomination}
            symbol={order.symbol}
            expiresAt={order.expires_at}
          />

          {order.units > 1 && (
            <div style={{
              marginTop: 12, padding: 10, background: '#0f0f1a', borderRadius: 8,
              border: '1px solid #2a2a3e', textAlign: 'center',
            }}>
              <span style={{ color: '#888', fontSize: 13 }}>
                {order.units} units x {order.denomination_display} = {order.total_amount_display}
              </span>
            </div>
          )}

          <div style={{ marginTop: 24 }}>
            <h3 style={{ color: '#ccc', fontSize: 16, marginBottom: 12 }}>3. Submit Transaction Hash</h3>
            <p style={{ color: '#888', fontSize: 13, marginBottom: 8 }}>
              After sending, paste your transaction hash here.
            </p>
            <input value={txHash} onChange={(e) => setTxHash(e.target.value)}
              placeholder="0x... / txid..." style={{
                width: '100%', padding: '12px 16px', background: '#1a1a2e', border: '1px solid #2a2a3e',
                borderRadius: 8, color: '#fff', fontSize: 14, outline: 'none', boxSizing: 'border-box',
              }} />
            <button onClick={handleSubmitTx} disabled={loading || !txHash}
              style={{
                marginTop: 12, width: '100%', padding: '14px', background: loading ? '#555' : '#4caf50',
                color: '#fff', border: 'none', borderRadius: 8, fontSize: 16, fontWeight: 700,
                cursor: loading ? 'default' : 'pointer',
              }}>
              {loading ? 'Submitting...' : 'Submit Hash'}
            </button>
          </div>

          <div style={{ textAlign: 'center', marginTop: 16 }}>
            <span style={{ color: '#888', fontSize: 13 }}>Order ID: </span>
            <code style={{ color: '#6c5ce7', fontSize: 13 }}>{order.order_id}</code>
          </div>
        </>
      )}

      {step === 'track' && order && (
        <>
          <div style={{ background: '#1a1a2e', borderRadius: 12, padding: 20, border: '1px solid #2a2a3e', marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
              <div>
                <div style={{ color: '#888', fontSize: 12 }}>Order</div>
                <code style={{ color: '#6c5ce7', fontSize: 13 }}>{order.order_id}</code>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ color: '#888', fontSize: 12 }}>Status</div>
                <div style={{
                  color: order.status === 'completed' ? '#4caf50' : order.status === 'failed' ? '#f44336' : '#ffc107',
                  fontSize: 14, fontWeight: 700
                }}>{order.status.replace(/_/g, ' ').toUpperCase()}</div>
              </div>
            </div>

            {order.units > 1 && (
              <div style={{
                marginBottom: 16, padding: 10, background: '#0f0f1a', borderRadius: 8,
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              }}>
                <span style={{ color: '#888', fontSize: 13 }}>Units processed:</span>
                <span style={{ color: '#00d2ff', fontSize: 14, fontWeight: 700 }}>
                  {order.completed_units} / {order.units}
                </span>
              </div>
            )}

            <OrderProgress steps={order.steps || []} />
          </div>

          {order.status === 'completed' && (
            <div style={{ background: 'rgba(76,175,80,0.1)', border: '1px solid #4caf50', borderRadius: 8, padding: 16, textAlign: 'center' }}>
              <div style={{ color: '#4caf50', fontSize: 18, fontWeight: 700, marginBottom: 4 }}>Mixing Complete!</div>
              <div style={{ color: '#888', fontSize: 13 }}>
                {order.payout_display || order.payout_amount} sent to {order.recipient_address.slice(0, 10)}...
              </div>
            </div>
          )}

          <button onClick={handleReset} style={{
            marginTop: 20, width: '100%', padding: '12px', background: 'transparent',
            color: '#888', border: '1px solid #2a2a3e', borderRadius: 8, cursor: 'pointer', fontSize: 14,
          }}>
            New Mix
          </button>
        </>
      )}
    </div>
  )
}
