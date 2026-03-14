import { useState, useEffect, useCallback } from 'react'
import type { NetworkMode, AdminStats, AdminPool, MixOrder, SeedJob, AdminBalances, WalletAddresses, FeeWallets, FeeWalletSet } from '../types'
import {
  adminLogin, adminGetStats, adminGetOrders, adminGetPools,
  adminSeedPool, adminGetSeedStatus, adminGetBalances,
  adminInitPools, adminGetWallet, adminUpdateWallet, adminUpdatePool,
  adminGetFeeWallets, adminUpdateFeeWallets,
} from '../services/api'

// ── Статусы ───────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  pending_payment:   '#ffc107',
  payment_detected:  '#ff9800',
  payment_confirmed: '#ff9800',
  depositing:        '#2196f3',
  deposited:         '#2196f3',
  proving:           '#9c27b0',
  withdrawing:       '#00bcd4',
  completed:         '#4caf50',
  failed:            '#f44336',
  expired:           '#607d8b',
}

const STATUS_RU: Record<string, string> = {
  pending_payment:   'ожидание оплаты',
  payment_detected:  'оплата обнаружена',
  payment_confirmed: 'оплата подтверждена',
  depositing:        'депозит отправляется',
  deposited:         'депозит внесён',
  proving:           'генерация доказательства',
  withdrawing:       'вывод средств',
  completed:         'завершён',
  failed:            'ошибка',
  expired:           'истёк',
}

function StatusChip({ status }: { status: string }) {
  const color = STATUS_COLORS[status] || '#888'
  return (
    <span style={{
      background: color + '22', color, border: `1px solid ${color}44`,
      borderRadius: 12, padding: '2px 8px', fontSize: 11, fontWeight: 600,
      whiteSpace: 'nowrap',
    }}>
      {STATUS_RU[status] || status}
    </span>
  )
}

// ── Общие стили ───────────────────────────────────────────────────────────────

const card: React.CSSProperties = {
  background: '#0f0f1a', border: '1px solid #1a1a2e', borderRadius: 10,
  padding: '20px 24px', marginBottom: 16,
}

const input: React.CSSProperties = {
  background: '#0a0a0f', border: '1px solid #2a2a3e', borderRadius: 6,
  color: '#e0e0e0', padding: '8px 12px', fontSize: 14, width: '100%', boxSizing: 'border-box',
}

const btn = (variant: 'primary' | 'ghost' | 'danger' = 'primary'): React.CSSProperties => ({
  background: variant === 'primary' ? '#6c5ce7' : variant === 'danger' ? '#f44336' : 'transparent',
  color: variant === 'ghost' ? '#888' : '#fff',
  border: variant === 'ghost' ? '1px solid #333' : 'none',
  borderRadius: 6, padding: '8px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600,
})

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text).catch(() => {})
}

// ── Вкладка «Обзор» ──────────────────────────────────────────────────────────

function OverviewTab({ stats }: { stats: AdminStats | null }) {
  if (!stats) return <p style={{ color: '#888' }}>Загрузка…</p>

  const active = ['pending_payment','payment_detected','payment_confirmed',
    'depositing','deposited','proving','withdrawing']
    .reduce((s, k) => s + (stats.orders_by_status[k] || 0), 0)

  const completed = stats.orders_by_status['completed'] || 0
  const failed = (stats.orders_by_status['failed'] || 0) + (stats.orders_by_status['expired'] || 0)

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px,1fr))', gap: 12, marginBottom: 24 }}>
        {[
          { label: 'Активных заказов', value: active, color: '#2196f3' },
          { label: 'Завершено', value: completed, color: '#4caf50' },
          { label: 'Ошибки / Истекло', value: failed, color: '#f44336' },
          { label: 'Всего за всё время', value: stats.total_orders, color: '#9c27b0' },
        ].map(({ label, value, color }) => (
          <div key={label} style={{ ...card, marginBottom: 0 }}>
            <div style={{ color, fontSize: 32, fontWeight: 700 }}>{value}</div>
            <div style={{ color: '#888', fontSize: 13, marginTop: 4 }}>{label}</div>
          </div>
        ))}
      </div>

      <h3 style={{ color: '#ccc', marginBottom: 12, fontSize: 15 }}>Состояние пулов</h3>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #1a1a2e', color: '#888' }}>
              {['Символ', 'Сеть', 'Режим', 'Доступно', 'Зарезерв.', 'Выведено'].map(h => (
                <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {stats.pools.map((p, i) => (
              <tr key={i} style={{ borderBottom: '1px solid #0d0d1a' }}>
                <td style={{ padding: '8px 12px', fontWeight: 600 }}>{p.symbol}</td>
                <td style={{ padding: '8px 12px', color: '#aaa' }}>{p.chain}</td>
                <td style={{ padding: '8px 12px' }}>
                  <span style={{ color: p.network_mode === 'mainnet' ? '#4caf50' : '#ffc107', fontSize: 11 }}>
                    {p.network_mode === 'mainnet' ? 'основная' : 'тестовая'}
                  </span>
                </td>
                <td style={{ padding: '8px 12px', color: '#4caf50' }}>{p.available}</td>
                <td style={{ padding: '8px 12px', color: '#ffc107' }}>{p.reserved}</td>
                <td style={{ padding: '8px 12px', color: '#888' }}>{p.withdrawn}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Вкладка «Заказы» ─────────────────────────────────────────────────────────

function OrdersTab({ token }: { token: string }) {
  const [orders, setOrders] = useState<MixOrder[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [statusFilter, setStatusFilter] = useState('')
  const [symbolFilter, setSymbolFilter] = useState('')
  const [modeFilter, setModeFilter] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const LIMIT = 50

  const fetch = useCallback(async () => {
    setLoading(true)
    try {
      const res = await adminGetOrders(token, {
        status: statusFilter || undefined,
        symbol: symbolFilter || undefined,
        network_mode: modeFilter || undefined,
        limit: LIMIT,
        offset,
      })
      setOrders(res.orders)
      setTotal(res.total)
    } catch {
      // обработка тихая — истечение токена перенаправит через родительский компонент
    } finally {
      setLoading(false)
    }
  }, [token, statusFilter, symbolFilter, modeFilter, offset])

  useEffect(() => { fetch() }, [fetch])

  const pages = Math.ceil(total / LIMIT)
  const page = Math.floor(offset / LIMIT)

  return (
    <div>
      {/* Фильтры */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        <select
          value={statusFilter}
          onChange={e => { setStatusFilter(e.target.value); setOffset(0) }}
          style={{ ...input, width: 220 }}
        >
          <option value="">Все статусы</option>
          {Object.entries(STATUS_RU).map(([val, label]) => (
            <option key={val} value={val}>{label}</option>
          ))}
        </select>
        <input
          style={{ ...input, width: 140 }}
          placeholder="Символ (ETH…)"
          value={symbolFilter}
          onChange={e => { setSymbolFilter(e.target.value.toUpperCase()); setOffset(0) }}
        />
        <select
          value={modeFilter}
          onChange={e => { setModeFilter(e.target.value); setOffset(0) }}
          style={{ ...input, width: 160 }}
        >
          <option value="">Все сети</option>
          <option value="mainnet">основная сеть</option>
          <option value="testnet">тестовая сеть</option>
        </select>
        <button style={btn('primary')} onClick={fetch}>Обновить</button>
      </div>

      {loading && <p style={{ color: '#888' }}>Загрузка…</p>}

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #1a1a2e', color: '#888' }}>
              {['ID', 'Актив', 'Сеть', 'Статус', 'Получатель', 'Сумма', 'Создан'].map(h => (
                <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {orders.map(o => (
              <>
                <tr
                  key={o.order_id}
                  onClick={() => setExpanded(expanded === o.order_id ? null : o.order_id)}
                  style={{ borderBottom: '1px solid #0d0d1a', cursor: 'pointer' }}
                >
                  <td style={{ padding: '8px 12px', fontFamily: 'monospace', color: '#6c5ce7' }}>
                    {o.order_id.slice(0, 8)}…
                  </td>
                  <td style={{ padding: '8px 12px', fontWeight: 600 }}>
                    {o.symbol}<span style={{ color: '#888', fontWeight: 400 }}> / {o.chain}</span>
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    <span style={{ color: o.network_mode === 'mainnet' ? '#4caf50' : '#ffc107', fontSize: 11 }}>
                      {o.network_mode === 'mainnet' ? 'основная' : 'тестовая'}
                    </span>
                  </td>
                  <td style={{ padding: '8px 12px' }}><StatusChip status={o.status} /></td>
                  <td style={{ padding: '8px 12px', fontFamily: 'monospace', color: '#aaa' }}>
                    {o.recipient_address.slice(0, 12)}…
                  </td>
                  <td style={{ padding: '8px 12px', color: '#e0e0e0' }}>{o.total_amount_display || o.total_amount}</td>
                  <td style={{ padding: '8px 12px', color: '#888' }}>
                    {new Date(o.created_at).toLocaleString('ru-RU')}
                  </td>
                </tr>
                {expanded === o.order_id && (
                  <tr key={o.order_id + '-detail'}>
                    <td colSpan={7} style={{ background: '#080810', padding: '12px 24px' }}>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 32px', fontSize: 12, fontFamily: 'monospace' }}>
                        {[
                          ['ID заказа', o.order_id],
                          ['Получатель', o.recipient_address],
                          ['Адрес сервиса', o.service_address],
                          ['Юниты', `${o.completed_units}/${o.units}`],
                          ['Выплата', o.payout_amount || '—'],
                          ['TX пользователя', o.user_tx_hash || '—'],
                          ['TX депозита', o.deposit_tx_hash || '—'],
                          ['TX вывода', o.withdraw_tx_hash || '—'],
                          ['Ошибка', o.error_message || '—'],
                          ['Истекает', o.expires_at],
                          ['Выведен', o.withdrawn_at || '—'],
                        ].map(([k, v]) => (
                          <div key={k}>
                            <span style={{ color: '#666' }}>{k}: </span>
                            <span style={{ color: '#ccc', wordBreak: 'break-all' }}>{v}</span>
                          </div>
                        ))}
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>

      {/* Пагинация */}
      {pages > 1 && (
        <div style={{ display: 'flex', gap: 8, marginTop: 16, alignItems: 'center' }}>
          <button style={btn('ghost')} disabled={page === 0} onClick={() => setOffset(0)}>«</button>
          <button style={btn('ghost')} disabled={page === 0} onClick={() => setOffset((page - 1) * LIMIT)}>‹</button>
          <span style={{ color: '#888', fontSize: 13 }}>Стр. {page + 1} / {pages} ({total} заказов)</span>
          <button style={btn('ghost')} disabled={page >= pages - 1} onClick={() => setOffset((page + 1) * LIMIT)}>›</button>
          <button style={btn('ghost')} disabled={page >= pages - 1} onClick={() => setOffset((pages - 1) * LIMIT)}>»</button>
        </div>
      )}
    </div>
  )
}

// ── Виджет пополнения пула ────────────────────────────────────────────────────

function SeedWidget({ token, pool }: { token: string; pool: AdminPool }) {
  const [units, setUnits] = useState(5)
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<SeedJob | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!jobId) return
    const interval = setInterval(async () => {
      try {
        const status = await adminGetSeedStatus(token, jobId)
        setJob(status)
        if (!status.running && status.done + status.failed >= status.total) {
          clearInterval(interval)
        }
      } catch {
        clearInterval(interval)
      }
    }, 2000)
    return () => clearInterval(interval)
  }, [token, jobId])

  async function startSeed() {
    setError('')
    setJob(null)
    try {
      const res = await adminSeedPool(token, {
        symbol: pool.symbol,
        chain: pool.chain,
        network_mode: pool.network_mode,
        units,
      })
      setJobId(res.job_id)
    } catch (e: unknown) {
      setError((e as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Ошибка запуска пополнения')
    }
  }

  const pct = job && job.total > 0 ? Math.round(((job.done + job.failed) / job.total) * 100) : 0
  const isDone = job && !job.running && job.done + job.failed >= job.total

  const isAnchor = pool.symbol === 'BTC_ANCHOR'
  const denomWei = BigInt(pool.denomination)
  const totalWei = denomWei * BigInt(units)
  const totalEth = Number(totalWei) / 1e18

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid #1a1a2e' }}>
      {!jobId ? (
        <>
          <div style={{ fontSize: 11, color: '#888', marginBottom: 10, lineHeight: 1.5 }}>
            {isAnchor ? (
              <>
                <strong style={{ color: '#a29bfe' }}>zkSNARK-слот</strong> = один депозит 1 wei в контракт Miximus на {pool.chain === 'polygon' ? 'Polygon' : 'Sepolia'}.<br />
                Каждый слот расходуется при обработке одного BTC-заказа для генерации анонимного доказательства.<br />
                Стоимость: только газ (~0.001–0.003 ETH/MATIC за слот).
              </>
            ) : (
              <>
                <strong style={{ color: '#fdcb6e' }}>Юнит</strong> = депозит <code style={{ background: '#1a1a2e', padding: '1px 4px', borderRadius: 3 }}>{pool.denomination} wei</code> в контракт миксера.<br />
                Пользователи будут выводить из этих юнитов — их депозиты пойдут в пул, а не обратно к ним.<br />
                {units > 0 && <span>Итого спишется с сервисного кошелька: <strong style={{ color: '#fff' }}>{totalEth.toFixed(6)} {pool.chain === 'tron' ? 'TRX' : 'ETH'}</strong> + газ.</span>}
              </>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            type="number" min={1} max={50} value={units}
            onChange={e => setUnits(parseInt(e.target.value) || 1)}
            style={{ ...input, width: 80 }}
          />
          <span style={{ color: '#888', fontSize: 13 }}>юнитов</span>
          <button style={btn('primary')} onClick={startSeed}>Пополнить</button>
          {error && <span style={{ color: '#f44336', fontSize: 12 }}>{error}</span>}
        </div>
        </>
      ) : (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, fontSize: 12, color: '#888' }}>
            <span>
              {isDone
                ? (job!.failed > 0 ? `Завершено с ${job!.failed} ошибками` : 'Готово!')
                : `Пополнение… ${job?.done ?? 0}/${job?.total ?? units}`}
            </span>
            <span>{pct}%</span>
          </div>
          <div style={{ background: '#1a1a2e', borderRadius: 4, height: 6 }}>
            <div style={{
              height: '100%', borderRadius: 4,
              background: isDone && job!.failed > 0 ? '#f44336' : '#6c5ce7',
              width: `${pct}%`, transition: 'width 0.3s',
            }} />
          </div>
          {job?.errors?.length ? (
            <div style={{ marginTop: 6, fontSize: 11, color: '#f44336' }}>
              {job.errors.slice(-2).join(' | ')}
            </div>
          ) : null}
          {isDone && (
            <button style={{ ...btn('ghost'), marginTop: 8, fontSize: 11 }} onClick={() => { setJobId(null); setJob(null) }}>
              Пополнить ещё
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── Редактор адреса пула ──────────────────────────────────────────────────────

function AddressEditor({
  token, pool, onSaved,
}: { token: string; pool: AdminPool; onSaved: (updated: AdminPool) => void }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(pool.service_wallet_address)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function save() {
    setSaving(true)
    setError('')
    try {
      const updated = await adminUpdatePool(token, pool.id, { service_wallet_address: value.trim() })
      onSaved(updated)
      setEditing(false)
    } catch (e: unknown) {
      setError((e as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Ошибка сохранения')
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, color: '#555' }}>Адрес получения:</span>
        <span style={{ fontSize: 11, fontFamily: 'monospace', color: '#888',
          maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {pool.service_wallet_address}
        </span>
        <button style={{ ...btn('ghost'), padding: '2px 8px', fontSize: 11 }} onClick={() => {
          setValue(pool.service_wallet_address)
          setEditing(true)
        }}>
          ✎ Изменить
        </button>
      </div>
    )
  }

  return (
    <div style={{ marginTop: 10 }}>
      <label style={{ display: 'block', fontSize: 11, color: '#888', marginBottom: 4 }}>
        Адрес получения платежей
      </label>
      <input
        style={{ ...input, fontSize: 12, fontFamily: 'monospace' }}
        value={value}
        onChange={e => setValue(e.target.value)}
        placeholder="Введите адрес…"
        autoFocus
      />
      {error && <div style={{ color: '#f44336', fontSize: 11, marginTop: 4 }}>{error}</div>}
      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        <button style={{ ...btn('primary'), padding: '5px 12px', fontSize: 12 }}
          onClick={save} disabled={saving || !value.trim()}>
          {saving ? 'Сохранение…' : 'Сохранить'}
        </button>
        <button style={{ ...btn('ghost'), padding: '5px 12px', fontSize: 12 }}
          onClick={() => { setEditing(false); setError('') }}>
          Отмена
        </button>
      </div>
    </div>
  )
}

// ── Вкладка «Пулы» ───────────────────────────────────────────────────────────

function PoolsTab({ token }: { token: string }) {
  const [pools, setPools] = useState<AdminPool[]>([])
  const [seedingPool, setSeedingPool] = useState<number | null>(null)
  const [initStatus, setInitStatus] = useState<string>('')
  const [initLoading, setInitLoading] = useState(false)
  const [bulkUnits, setBulkUnits] = useState(5)
  const [bulkStatus, setBulkStatus] = useState<{ done: number; failed: number; total: number } | null>(null)
  const [bulkLoading, setBulkLoading] = useState(false)

  const loadPools = useCallback(() => {
    adminGetPools(token).then(r => setPools(r.pools)).catch(() => {})
  }, [token])

  useEffect(() => { loadPools() }, [loadPools])

  async function handleInitPools() {
    setInitLoading(true)
    setInitStatus('')
    try {
      const res = await adminInitPools(token)
      setInitStatus(`Готово: создано ${res.created}, обновлено ${res.updated} из ${res.total} пулов.`)
      loadPools()
    } catch (e: unknown) {
      setInitStatus((e as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Ошибка инициализации')
    } finally {
      setInitLoading(false)
    }
  }

  async function handleSeedAll() {
    const targets = pools.filter(p => p.mixer_contract !== 'custodial' && p.symbol !== 'BTC_ANCHOR' && p.enabled)
    if (targets.length === 0) return
    setBulkLoading(true)
    setBulkStatus({ done: 0, failed: 0, total: targets.length })
    let done = 0, failed = 0
    for (const pool of targets) {
      try {
        await adminSeedPool(token, {
          symbol: pool.symbol, chain: pool.chain,
          network_mode: pool.network_mode, units: bulkUnits,
        })
        done++
      } catch {
        failed++
      }
      setBulkStatus({ done, failed, total: targets.length })
    }
    setBulkLoading(false)
    loadPools()
  }

  return (
    <div>
      {/* ── Инициализация пулов ── */}
      <div style={{ ...card, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>Инициализировать пулы</div>
          <div style={{ fontSize: 12, color: '#888' }}>
            Создаёт или обновляет конфигурации пулов в базе данных из встроенных определений.
            Запустите при первом старте или после добавления нового контракта.
          </div>
          {initStatus && (
            <div style={{ marginTop: 6, fontSize: 12, color: initStatus.startsWith('Готово') ? '#4caf50' : '#f44336' }}>
              {initStatus}
            </div>
          )}
        </div>
        <button style={btn('primary')} onClick={handleInitPools} disabled={initLoading}>
          {initLoading ? 'Загрузка…' : '⚙ Инициализировать пулы'}
        </button>
      </div>

      {/* ── Массовое пополнение ── */}
      <div style={{ ...card, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>Пополнить пулы EVM/Tron</div>
          <div style={{ fontSize: 12, color: '#888' }}>
            Депозит реальных средств в контракты миксера (ETH, USDC, USDT).
            BTC_ANCHOR и кастодиальные пулы пропускаются — пополняйте их отдельно.
          </div>
          {bulkStatus && (
            <div style={{ marginTop: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#888', marginBottom: 4 }}>
                <span>
                  {bulkLoading
                    ? `Пополнение… ${bulkStatus.done + bulkStatus.failed}/${bulkStatus.total}`
                    : bulkStatus.failed > 0
                      ? `Завершено: ${bulkStatus.done} OK, ${bulkStatus.failed} ошибок`
                      : `Все ${bulkStatus.done} пулов запущены`}
                </span>
                <span>{Math.round(((bulkStatus.done + bulkStatus.failed) / bulkStatus.total) * 100)}%</span>
              </div>
              <div style={{ background: '#1a1a2e', borderRadius: 4, height: 6 }}>
                <div style={{
                  height: '100%', borderRadius: 4,
                  background: !bulkLoading && bulkStatus.failed > 0 ? '#f44336' : '#6c5ce7',
                  width: `${Math.round(((bulkStatus.done + bulkStatus.failed) / bulkStatus.total) * 100)}%`,
                  transition: 'width 0.3s',
                }} />
              </div>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            type="number" min={1} max={50} value={bulkUnits}
            onChange={e => setBulkUnits(Math.max(1, parseInt(e.target.value) || 1))}
            style={{ ...input, width: 70, textAlign: 'center' }}
          />
          <span style={{ color: '#888', fontSize: 12, whiteSpace: 'nowrap' }}>юн./пул</span>
          <button
            style={btn('primary')}
            onClick={handleSeedAll}
            disabled={bulkLoading || pools.filter(p => p.mixer_contract !== 'custodial' && p.symbol !== 'BTC_ANCHOR' && p.enabled).length === 0}
          >
            {bulkLoading ? 'Пополнение…' : '⬆ Пополнить EVM/Tron'}
          </button>
        </div>
      </div>

      {/* ── Карточки пулов ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px,1fr))', gap: 16 }}>
        {pools.map(pool => (
          <div key={pool.id} style={card}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
              <div>
                <span style={{ fontWeight: 700, fontSize: 16 }}>{pool.symbol}</span>
                <span style={{ color: '#888', fontSize: 13, marginLeft: 8 }}>{pool.chain}</span>
                <span style={{
                  marginLeft: 6, fontSize: 11,
                  color: pool.network_mode === 'mainnet' ? '#4caf50' : '#ffc107',
                }}>
                  {pool.network_mode === 'mainnet' ? 'основная' : 'тестовая'}
                </span>
              </div>
              <span style={{
                background: pool.enabled ? 'rgba(76,175,80,0.15)' : 'rgba(244,67,54,0.15)',
                color: pool.enabled ? '#4caf50' : '#f44336',
                fontSize: 11, padding: '2px 8px', borderRadius: 10, fontWeight: 600,
              }}>
                {pool.enabled ? 'АКТИВЕН' : 'ОТКЛЮЧЁН'}
              </span>
            </div>

            <div style={{ fontSize: 12, color: '#888', marginBottom: 8, fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {pool.mixer_contract}
            </div>
            <div style={{ fontSize: 12, color: '#aaa', marginBottom: 10 }}>
              Номинал: {pool.denomination} wei &nbsp;·&nbsp; Комиссия: {(pool.commission_rate * 100).toFixed(1)}%
            </div>

            {pool.mixer_contract === 'custodial' ? (
              <div style={{ fontSize: 12, color: '#777', fontStyle: 'italic' }}>
                Кастодиальный — ёмкость = баланс кошелька
              </div>
            ) : (
              <>
                <div style={{ display: 'flex', gap: 16, fontSize: 13 }}>
                  <span style={{ color: '#4caf50' }}>✓ {pool.available} доступно</span>
                  <span style={{ color: '#ffc107' }}>⏳ {pool.reserved} зарезерв.</span>
                  <span style={{ color: '#666' }}>✗ {pool.withdrawn} выведено</span>
                </div>
                <div style={{ fontSize: 11, color: '#666', marginTop: 4 }}>
                  {pool.symbol === 'BTC_ANCHOR'
                    ? '🔐 zkSNARK-слоты для анонимности BTC-ордеров'
                    : `💰 Каждый юнит = депозит ${pool.denomination} wei в контракт миксера`}
                </div>
              </>
            )}

            {/* Address editor — shown for all pool types */}
            <AddressEditor
              token={token}
              pool={pool}
              onSaved={updated => setPools(prev => prev.map(p => p.id === updated.id
                ? { ...p, service_wallet_address: updated.service_wallet_address }
                : p
              ))}
            />

            {pool.mixer_contract !== 'custodial' && pool.enabled ? (
              <>
                <button
                  style={{ ...btn('ghost'), marginTop: 10, fontSize: 12 }}
                  onClick={() => setSeedingPool(seedingPool === pool.id ? null : pool.id)}
                >
                  {seedingPool === pool.id
                    ? 'Отмена'
                    : pool.symbol === 'BTC_ANCHOR'
                      ? '+ Добавить zkSNARK-слоты'
                      : '+ Пополнить пул (депозит в контракт)'}
                </button>
                {seedingPool === pool.id && <SeedWidget token={token} pool={pool} />}
              </>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Вкладка «Финансирование» ─────────────────────────────────────────────────

function FundingTab({ token, networkMode }: { token: string; networkMode: 'mainnet' | 'testnet' }) {
  const [balances, setBalances] = useState<AdminBalances | null>(null)
  const [pools, setPools] = useState<AdminPool[]>([])
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)

  async function refresh() {
    setLoading(true)
    try {
      const [bal, poolsRes] = await Promise.all([
        adminGetBalances(token),
        adminGetPools(token),
      ])
      setBalances(bal)
      setPools(poolsRes.pools)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  function copy(text: string, key: string) {
    copyToClipboard(text)
    setCopied(key)
    setTimeout(() => setCopied(null), 2000)
  }

  function getPoolDenomination(chain: string, networkMode: string): string | null {
    const pool = pools.find(p => p.chain === chain && p.network_mode === networkMode)
    return pool?.denomination ?? null
  }

  function AddressCard({ title, address, balance, balanceLabel, note, copyKey, denomination, decimals }: {
    title: string; address: string; balance: string; balanceLabel: string
    note?: string; copyKey: string; denomination?: string | null; decimals?: number
  }) {
    const [units, setUnits] = useState(10)
    const dec = decimals ?? 8
    const denNum = denomination ? parseInt(denomination) : 0
    const costPerUnit = denNum / Math.pow(10, dec)
    const totalCost = units * costPerUnit

    return (
      <div style={card}>
        <h3 style={{ margin: '0 0 12px', fontSize: 15, color: '#ccc' }}>{title}</h3>
        <div style={{ fontFamily: 'monospace', fontSize: 13, color: '#aaa', marginBottom: 8, wordBreak: 'break-all' }}>
          {address}
        </div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <button style={btn('ghost')} onClick={() => copy(address, copyKey)}>
            {copied === copyKey ? '✓ Скопировано' : 'Скопировать адрес'}
          </button>
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, color: '#e0e0e0' }}>
          {balance} <span style={{ fontSize: 14, color: '#888' }}>{balanceLabel}</span>
        </div>
        {note && <p style={{ margin: '10px 0 0', fontSize: 12, color: '#666' }}>{note}</p>}

        {denomination && (
          <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid #1a1a2e' }}>
            <div style={{ fontSize: 11, color: '#666', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Калькулятор пополнения
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <input
                type="number" min={1} max={9999} value={units}
                onChange={e => setUnits(Math.max(1, parseInt(e.target.value) || 1))}
                style={{ ...input, width: 90, textAlign: 'center' }}
              />
              <span style={{ color: '#888', fontSize: 13 }}>юнитов</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', color: '#888' }}>
                <span>Стоимость 1 юнита</span>
                <span style={{ fontFamily: 'monospace', color: '#ccc' }}>
                  {denNum.toLocaleString()} sat&nbsp;
                  <span style={{ color: '#555' }}>({costPerUnit.toFixed(dec)} {balanceLabel})</span>
                </span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', paddingTop: 6, borderTop: '1px solid #1a1a2e' }}>
                <span style={{ color: '#aaa', fontWeight: 600 }}>Итого для {units} юнитов</span>
                <span style={{ fontFamily: 'monospace', color: '#6c5ce7', fontWeight: 700 }}>
                  {(units * denNum).toLocaleString()} sat&nbsp;
                  <span style={{ color: '#888', fontWeight: 400 }}>({totalCost.toFixed(dec)} {balanceLabel})</span>
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    )
  }

  const isMainnet = networkMode === 'mainnet'

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16 }}>
        <button style={btn('ghost')} onClick={refresh} disabled={loading}>
          {loading ? 'Обновление…' : '↻ Обновить балансы'}
        </button>
      </div>

      {!balances ? (
        <p style={{ color: '#888' }}>{loading ? 'Загрузка…' : 'Ошибка загрузки балансов.'}</p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px,1fr))', gap: 16 }}>
          {isMainnet ? (
            <AddressCard
              title="Пул BTC (основная сеть)"
              address={balances.btc_mainnet.address}
              balance={balances.btc_mainnet.balance_btc}
              balanceLabel="BTC"
              copyKey="btc-main"
              denomination={getPoolDenomination('bitcoin', 'mainnet')}
              decimals={8}
            />
          ) : (
            <AddressCard
              title="Пул BTC (тестовая сеть)"
              address={balances.btc_testnet.address}
              balance={balances.btc_testnet.balance_btc}
              balanceLabel="tBTC"
              copyKey="btc-test"
              denomination={getPoolDenomination('bitcoin', 'testnet')}
              decimals={8}
            />
          )}
          <AddressCard
            title="Сервисный кошелёк EVM (Polygon / Ethereum)"
            address={balances.evm.address}
            balance={balances.evm.balance_matic}
            balanceLabel="MATIC"
            note="Используется для депозитов BTC_ANCHOR и обработки EVM-заказов. Пополните MATIC для оплаты газа."
            copyKey="evm"
          />
        </div>
      )}

      <WalletSettings token={token} networkMode={networkMode} />
    </div>
  )
}

// ── Редактор адреса BTC-пула (inline в WalletSettings) ───────────────────────

function BtcPoolAddressEditor({ token, pool, onSaved }: {
  token: string
  pool: AdminPool
  onSaved: (updated: AdminPool) => void
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(pool.service_wallet_address)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function save() {
    setSaving(true)
    setError('')
    try {
      const updated = await adminUpdatePool(token, pool.id, { service_wallet_address: value.trim() })
      onSaved(updated)
      setEditing(false)
    } catch (e: unknown) {
      setError((e as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Ошибка сохранения')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ padding: '12px 0', borderBottom: '1px solid #1a1a2e' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: editing ? 8 : 0 }}>
        <span style={{ fontSize: 13, color: '#888' }}>
          {pool.network_mode === 'mainnet' ? '₿ Основная сеть (BTC mainnet)' : '₿ Тестовая сеть (BTC testnet)'}
        </span>
        {!editing && (
          <button style={{ ...btn('ghost'), padding: '3px 10px', fontSize: 12 }}
            onClick={() => { setValue(pool.service_wallet_address); setEditing(true) }}>
            ✎ Изменить
          </button>
        )}
      </div>
      {!editing ? (
        <div style={{ fontFamily: 'monospace', fontSize: 12, color: '#ccc', marginTop: 4, wordBreak: 'break-all' }}>
          {pool.service_wallet_address}
        </div>
      ) : (
        <div>
          <input
            style={{ ...input, fontSize: 12, fontFamily: 'monospace' }}
            value={value}
            onChange={e => setValue(e.target.value)}
            placeholder="Введите Bitcoin-адрес…"
            autoFocus
          />
          {error && <div style={{ color: '#f44336', fontSize: 11, marginTop: 4 }}>{error}</div>}
          <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
            <button style={{ ...btn('primary'), padding: '5px 14px', fontSize: 12 }}
              onClick={save} disabled={saving || !value.trim()}>
              {saving ? 'Сохранение…' : 'Сохранить'}
            </button>
            <button style={{ ...btn('ghost'), padding: '5px 14px', fontSize: 12 }}
              onClick={() => { setEditing(false); setError('') }}>
              Отмена
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Настройки кошельков комиссий ─────────────────────────────────────────────

const EMPTY_FEE_SET: FeeWalletSet = { evm: '', tron: '', btc: '' }

function FeeWalletSettings({ token, networkMode }: { token: string; networkMode: 'mainnet' | 'testnet' }) {
  const [wallets, setWallets] = useState<FeeWallets>({
    mainnet: { ...EMPTY_FEE_SET },
    testnet: { ...EMPTY_FEE_SET },
  })
  const activeNet = networkMode
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)

  useEffect(() => {
    adminGetFeeWallets(token).then(setWallets).catch(() => {})
  }, [token])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setStatus(null)
    try {
      const res = await adminUpdateFeeWallets(token, wallets)
      setWallets({ mainnet: res.mainnet, testnet: res.testnet })
      setStatus({ type: 'success', msg: res.note })
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Ошибка сохранения'
      setStatus({ type: 'error', msg })
    } finally {
      setSaving(false)
    }
  }

  const fields: { key: keyof FeeWalletSet; label: string; placeholder: string }[] = [
    { key: 'evm',  label: 'EVM (Ethereum / Polygon / BSC…)', placeholder: '0x…' },
    { key: 'tron', label: 'Tron',                             placeholder: 'T…' },
    { key: 'btc',  label: 'Bitcoin',                          placeholder: activeNet === 'mainnet' ? '1… или bc1…' : 'tb1… или m…' },
  ]

  const current = wallets[activeNet]

  return (
    <div style={{ ...card, marginTop: 16 }}>
      <h3 style={{ margin: '0 0 6px', fontSize: 15, color: '#ccc' }}>Кошельки для комиссий</h3>
      <p style={{ margin: '0 0 16px', fontSize: 12, color: '#666' }}>
        После каждого вывода система пересылает комиссию на указанный адрес.
        Оставьте пустым — комиссия остаётся на горячем кошельке. Применяется немедленно.
      </p>

      {status && (
        <div style={{
          padding: '8px 12px', borderRadius: 8, marginBottom: 12, fontSize: 12,
          background: status.type === 'success' ? 'rgba(76,175,80,0.1)' : 'rgba(244,67,54,0.1)',
          color: status.type === 'success' ? '#4caf50' : '#f44336',
          border: `1px solid ${status.type === 'success' ? 'rgba(76,175,80,0.3)' : 'rgba(244,67,54,0.3)'}`,
        }}>
          {status.msg}
        </div>
      )}

      <form onSubmit={save}>
        {fields.map(({ key, label, placeholder }) => (
          <div key={key} style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#888', marginBottom: 4 }}>{label}</label>
            <input
              style={{ ...input, fontFamily: 'monospace' }}
              placeholder={placeholder}
              value={current[key]}
              onChange={e => setWallets(prev => ({
                ...prev,
                [activeNet]: { ...prev[activeNet], [key]: e.target.value },
              }))}
            />
          </div>
        ))}
        <button style={{ ...btn('primary'), marginTop: 4 }} type="submit" disabled={saving}>
          {saving ? 'Сохранение…' : 'Сохранить адреса комиссий'}
        </button>
      </form>
    </div>
  )
}

// ── Настройки кошелька ────────────────────────────────────────────────────────

function WalletSettings({ token, networkMode }: { token: string; networkMode: 'mainnet' | 'testnet' }) {
  const [walletInfo, setWalletInfo] = useState<WalletAddresses | null>(null)
  const [btcPools, setBtcPools] = useState<AdminPool[]>([])
  const [loadError, setLoadError] = useState('')
  const [showKeyForm, setShowKeyForm] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [keyStatus, setKeyStatus] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)
  const [updating, setUpdating] = useState(false)

  useEffect(() => {
    adminGetWallet(token)
      .then(setWalletInfo)
      .catch(() => setLoadError('Не удалось загрузить адреса горячего кошелька'))
    adminGetPools(token)
      .then(r => setBtcPools(r.pools.filter(p => p.chain === 'bitcoin')))
      .catch(() => {})
  }, [token])

  async function handleKeyUpdate(e: React.FormEvent) {
    e.preventDefault()
    setUpdating(true)
    setKeyStatus(null)
    try {
      const res = await adminUpdateWallet(token, newKey)
      setWalletInfo(res.addresses)
      setKeyStatus({ type: 'success', msg: res.note })
      setShowKeyForm(false)
      setNewKey('')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Ошибка обновления'
      setKeyStatus({ type: 'error', msg })
    } finally {
      setUpdating(false)
    }
  }

  const addrRow = (label: string, value: string | undefined) => (
    <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '8px 0', borderBottom: '1px solid #1a1a2e', fontSize: 13, gap: 16 }}>
      <span style={{ color: '#888', flexShrink: 0 }}>{label}</span>
      <span style={{ fontFamily: 'monospace', color: '#ccc', wordBreak: 'break-all', textAlign: 'right', fontSize: 12 }}>
        {value || '—'}
      </span>
    </div>
  )

  return (
    <div style={{ marginTop: 24 }}>

      {/* ── Секция 1: Адреса приёма BTC ── */}
      <div style={card}>
        <h3 style={{ margin: '0 0 6px', fontSize: 15, color: '#ccc' }}>Адреса приёма BTC</h3>
        <p style={{ margin: '0 0 16px', fontSize: 12, color: '#666' }}>
          Биткоин-адрес, который показывается пользователю для отправки монет.
          Укажите любой свой адрес — холодный кошелёк, Ledger, биржевой депозит и т.д.
          Менять приватный ключ системы не нужно.
        </p>

        {loadError && <p style={{ color: '#f44336', fontSize: 12 }}>{loadError}</p>}

        {btcPools.filter(p => p.network_mode === networkMode).length === 0 ? (
          <p style={{ color: '#555', fontSize: 12, fontStyle: 'italic' }}>
            Пулы BTC не найдены. Сначала перейдите во вкладку Пулы и нажмите «Инициализировать пулы».
          </p>
        ) : (
          btcPools
            .filter(p => p.network_mode === networkMode)
            .map(pool => (
              <BtcPoolAddressEditor
                key={pool.id}
                token={token}
                pool={pool}
                onSaved={updated => setBtcPools(prev =>
                  prev.map(p => p.id === updated.id
                    ? { ...p, service_wallet_address: updated.service_wallet_address }
                    : p
                  )
                )}
              />
            ))
        )}
      </div>

      {/* ── Секция 2: Горячий кошелёк (подпись транзакций) ── */}
      <div style={{ ...card, marginTop: 16 }}>
        <h3 style={{ margin: '0 0 6px', fontSize: 15, color: '#ccc' }}>Горячий кошелёк (движок системы)</h3>
        <p style={{ margin: '0 0 12px', fontSize: 12, color: '#666' }}>
          Приватный ключ, которым система подписывает транзакции EVM: депозиты в BTC_ANCHOR на Polygon,
          вывод средств по EVM-ордерам, комиссии за газ. <strong style={{ color: '#aaa' }}>Не путать с адресом приёма BTC выше</strong> —
          они настраиваются независимо.
        </p>

        {walletInfo && (
          <div style={{ marginBottom: 14 }}>
            {addrRow('EVM (Polygon / Ethereum)', walletInfo.evm_address)}
            {addrRow('Tron', walletInfo.tron_address)}
          </div>
        )}

        <div style={{ background: 'rgba(76,175,80,0.07)', border: '1px solid rgba(76,175,80,0.2)',
          borderRadius: 8, padding: '8px 12px', marginBottom: 14, fontSize: 12, color: '#aaa' }}>
          <strong style={{ color: '#4caf50' }}>Смена ключа не требует редеплоя BTC_ANCHOR.</strong>{' '}
          Контракт на Polygon не хранит адрес оператора — zkSNARK-проверка не привязана к конкретному адресу.
          Уже засеянные юниты остаются действительными.
        </div>

        {keyStatus && (
          <div style={{
            padding: '8px 12px', borderRadius: 8, marginBottom: 12, fontSize: 12,
            background: keyStatus.type === 'success' ? 'rgba(76,175,80,0.1)' : 'rgba(244,67,54,0.1)',
            color: keyStatus.type === 'success' ? '#4caf50' : '#f44336',
            border: `1px solid ${keyStatus.type === 'success' ? 'rgba(76,175,80,0.3)' : 'rgba(244,67,54,0.3)'}`,
          }}>
            {keyStatus.msg}
          </div>
        )}

        {!showKeyForm ? (
          <button style={btn('ghost')} onClick={() => setShowKeyForm(true)}>
            🔑 Сменить приватный ключ подписи
          </button>
        ) : (
          <form onSubmit={handleKeyUpdate}>
            <div style={{
              background: 'rgba(255,193,7,0.07)', border: '1px solid rgba(255,193,7,0.3)',
              borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontSize: 12, color: '#ffc107',
            }}>
              ⚠ На новом EVM-адресе должен быть MATIC для оплаты газа. После смены перезапустите Flask-сервер.
            </div>
            <div style={{ marginBottom: 10 }}>
              <label style={{ display: 'block', fontSize: 12, color: '#888', marginBottom: 4 }}>
                Новый приватный ключ (64 hex-символа)
              </label>
              <input
                style={{ ...input, fontFamily: 'monospace' }}
                type="password"
                placeholder="0x... или без префикса"
                value={newKey}
                onChange={e => setNewKey(e.target.value)}
                required
              />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={btn('primary')} type="submit" disabled={updating}>
                {updating ? 'Обновление…' : 'Применить'}
              </button>
              <button style={btn('ghost')} type="button"
                onClick={() => { setShowKeyForm(false); setNewKey('') }}>
                Отмена
              </button>
            </div>
          </form>
        )}
      </div>

      {/* ── Секция 3: Кошельки для комиссий ── */}
      <FeeWalletSettings token={token} networkMode={networkMode} />

    </div>
  )
}

// ── Форма входа ───────────────────────────────────────────────────────────────

function LoginForm({ onLogin }: { onLogin: (token: string) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const { token } = await adminLogin(username, password)
      sessionStorage.setItem('adminToken', token)
      onLogin(token)
    } catch {
      setError('Неверный логин или пароль')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ minHeight: '80vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ ...card, width: 340, padding: 32 }}>
        <h2 style={{ margin: '0 0 24px', fontSize: 20, color: '#e0e0e0', textAlign: 'center' }}>
          Вход в систему
        </h2>
        <form onSubmit={submit}>
          <div style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#888', marginBottom: 4 }}>Логин</label>
            <input
              style={input} type="text" autoComplete="username"
              value={username} onChange={e => setUsername(e.target.value)} required
            />
          </div>
          <div style={{ marginBottom: 20 }}>
            <label style={{ display: 'block', fontSize: 12, color: '#888', marginBottom: 4 }}>Пароль</label>
            <input
              style={input} type="password" autoComplete="current-password"
              value={password} onChange={e => setPassword(e.target.value)} required
            />
          </div>
          {error && <p style={{ color: '#f44336', fontSize: 13, margin: '0 0 12px' }}>{error}</p>}
          <button style={{ ...btn('primary'), width: '100%', padding: '10px' }} type="submit" disabled={loading}>
            {loading ? 'Вход…' : 'Войти'}
          </button>
        </form>
      </div>
    </div>
  )
}

// ── Главный компонент ─────────────────────────────────────────────────────────

type Tab = 'overview' | 'orders' | 'pools' | 'funding'

export default function AdminPage({ networkMode = 'mainnet' }: { networkMode?: NetworkMode }) {
  const [token, setToken] = useState<string | null>(() => sessionStorage.getItem('adminToken'))
  const [tab, setTab] = useState<Tab>('overview')
  const [stats, setStats] = useState<AdminStats | null>(null)

  useEffect(() => {
    if (!token) return
    adminGetStats(token)
      .then(setStats)
      .catch(() => {
        sessionStorage.removeItem('adminToken')
        setToken(null)
      })
  }, [token])

  function logout() {
    sessionStorage.removeItem('adminToken')
    setToken(null)
    setStats(null)
  }

  if (!token) {
    return <LoginForm onLogin={setToken} />
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'Обзор' },
    { id: 'orders',   label: 'Заказы' },
    { id: 'pools',    label: 'Пулы' },
    { id: 'funding',  label: 'Финансирование' },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div style={{ display: 'flex', gap: 4 }}>
          {tabs.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              style={{
                background: tab === t.id ? '#6c5ce7' : 'transparent',
                color: tab === t.id ? '#fff' : '#888',
                border: tab === t.id ? 'none' : '1px solid #333',
                borderRadius: 6, padding: '8px 18px', cursor: 'pointer', fontSize: 14, fontWeight: 600,
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
        <button style={btn('ghost')} onClick={logout}>Выйти</button>
      </div>

      {tab === 'overview' && <OverviewTab stats={stats} />}
      {tab === 'orders'   && <OrdersTab token={token} />}
      {tab === 'pools'    && <PoolsTab token={token} />}
      {tab === 'funding'  && <FundingTab token={token} networkMode={networkMode} />}
    </div>
  )
}
