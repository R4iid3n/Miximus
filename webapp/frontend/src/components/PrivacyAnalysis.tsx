import { useState, useEffect, Fragment, type ReactNode } from 'react'
import type { PrivacyAnalysisData } from '../types'
import { getOrderAnalysis } from '../services/api'

interface Props {
  orderId: string
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function AnalysisHeader({ analysis }: { analysis: PrivacyAnalysisData }) {
  const color = analysis.overall_passed ? '#4caf50' : '#ffc107'
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
      <div>
        <h3 style={{ color: '#fff', fontSize: 18, margin: 0 }}>Анализ приватности</h3>
        <p style={{ color: '#888', fontSize: 13, margin: '4px 0 0' }}>
          Ончейн-проверка вашей транзакции
        </p>
      </div>
      <div style={{
        background: `${color}22`, border: `2px solid ${color}`,
        borderRadius: 12, padding: '8px 16px', textAlign: 'center',
      }}>
        <div style={{ color, fontSize: 20, fontWeight: 700 }}>
          {analysis.passed_checks}/{analysis.total_checks}
        </div>
        <div style={{ color: '#888', fontSize: 11 }}>проверок</div>
      </div>
    </div>
  )
}

function TransactionFlow({ analysis }: { analysis: PrivacyAnalysisData }) {
  const addr = analysis.address_separation
  const nodes = [
    { label: 'Отправитель', address: addr.sender_address, color: '#6c5ce7' },
    { label: 'Сервис', address: addr.service_address, color: '#ffc107' },
    { label: 'Миксер', address: addr.mixer_contract, color: '#00d2ff' },
    { label: 'Получатель', address: addr.recipient_address, color: '#4caf50' },
  ]

  const truncate = (a: string | null) =>
    a ? `${a.slice(0, 6)}...${a.slice(-4)}` : 'Н/Д'

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: 16, background: '#0f0f1a', borderRadius: 12,
      border: '1px solid #2a2a3e', marginBottom: 16, overflowX: 'auto',
    }}>
      {nodes.map((node, i) => (
        <Fragment key={i}>
          {i > 0 && (
            <div style={{ color: '#555', fontSize: 18, margin: '0 4px', flexShrink: 0 }}>
              {'\u2192'}
            </div>
          )}
          <div style={{ textAlign: 'center', minWidth: 80, flexShrink: 0 }}>
            <div style={{ color: node.color, fontSize: 11, fontWeight: 600, marginBottom: 4 }}>
              {node.label}
            </div>
            <code style={{
              color: '#ccc', fontSize: 11, background: '#1a1a2e',
              padding: '4px 8px', borderRadius: 6, display: 'inline-block',
            }}>{truncate(node.address)}</code>
          </div>
        </Fragment>
      ))}
    </div>
  )
}

function CheckCard({ passed, title, subtitle, children }: {
  passed: boolean; title: string; subtitle: string; children: ReactNode
}) {
  const [expanded, setExpanded] = useState(false)
  const color = passed ? '#4caf50' : '#f44336'
  const icon = passed ? '\u2713' : '\u2717'

  return (
    <div style={{
      background: '#1a1a2e', borderRadius: 12, padding: 16,
      border: `1px solid ${passed ? '#2a2a3e' : '#f4433644'}`,
      marginBottom: 12,
    }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{ display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer' }}
      >
        <div style={{
          width: 28, height: 28, borderRadius: '50%',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 14, fontWeight: 700,
          background: `${color}22`, color, border: `2px solid ${color}`,
          flexShrink: 0,
        }}>{icon}</div>
        <div style={{ flex: 1 }}>
          <div style={{ color: '#fff', fontSize: 14, fontWeight: 600 }}>{title}</div>
          <div style={{ color: '#888', fontSize: 12 }}>{subtitle}</div>
        </div>
        <div style={{ color: '#555', fontSize: 14 }}>
          {expanded ? '\u25B2' : '\u25BC'}
        </div>
      </div>
      {expanded && (
        <div style={{
          marginTop: 12, paddingTop: 12, borderTop: '1px solid #2a2a3e',
          color: '#aaa', fontSize: 13, lineHeight: 1.6,
        }}>
          {children}
        </div>
      )}
    </div>
  )
}

function AddressRow({ label, address, color }: { label: string; address: string | null; color: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
      <span style={{ color: '#888', fontSize: 12 }}>{label}</span>
      <code style={{ color, fontSize: 12 }}>
        {address || 'Не определён'}
      </code>
    </div>
  )
}

function TxRow({ label, hash }: { label: string; hash: string | null }) {
  if (!hash) return null
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
      <span style={{ color: '#888', fontSize: 12 }}>{label}</span>
      <code style={{ color: '#6c5ce7', fontSize: 11 }}>
        {hash.slice(0, 16)}...{hash.slice(-6)}
      </code>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export default function PrivacyAnalysis({ orderId }: Props) {
  const [analysis, setAnalysis] = useState<PrivacyAnalysisData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getOrderAnalysis(orderId)
      .then((data) => { if (!cancelled) setAnalysis(data) })
      .catch(() => { /* analysis is supplementary — silently fail */ })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [orderId])

  if (loading) {
    return (
      <div style={{
        background: '#1a1a2e', borderRadius: 12, padding: 20,
        border: '1px solid #2a2a3e', textAlign: 'center', color: '#888', fontSize: 14,
      }}>
        Загрузка анализа приватности...
      </div>
    )
  }

  if (!analysis) return null

  const addr = analysis.address_separation

  return (
    <div style={{
      background: '#12121f', borderRadius: 16, padding: 24,
      border: '1px solid #2a2a3e',
    }}>
      <AnalysisHeader analysis={analysis} />
      <TransactionFlow analysis={analysis} />

      {/* Check 1: Address Separation */}
      <CheckCard
        passed={addr.passed}
        title="Разделение адресов"
        subtitle={addr.all_different ? '4 различных адреса — связь отсутствует' : 'Обнаружено совпадение адресов'}
      >
        <AddressRow label="Отправитель" address={addr.sender_address} color="#6c5ce7" />
        <AddressRow label="Сервис" address={addr.service_address} color="#ffc107" />
        <AddressRow label="Контракт миксера" address={addr.mixer_contract} color="#00d2ff" />
        <AddressRow label="Получатель" address={addr.recipient_address} color="#4caf50" />
      </CheckCard>

      {/* Check 2: Anonymity Set */}
      <CheckCard
        passed={analysis.anonymity_set.passed}
        title="Набор анонимности"
        subtitle={`${analysis.anonymity_set.total_deposits} депозитов в пуле`}
      >
        <div style={{ color: '#aaa' }}>
          Ваш вывод неотличим от <span style={{ color: '#00d2ff', fontWeight: 700 }}>
            {analysis.anonymity_set.total_deposits}
          </span> других депозитов в пуле {analysis.anonymity_set.pool_description}.
          Чем больше набор анонимности, тем выше уровень приватности.
        </div>
      </CheckCard>

      {/* Check 3: Denomination Uniformity */}
      <CheckCard
        passed={analysis.denomination_uniformity.passed}
        title="Единый номинал"
        subtitle={analysis.denomination_uniformity.denomination_display}
      >
        <div style={{ color: '#aaa' }}>{analysis.denomination_uniformity.explanation}</div>
      </CheckCard>

      {/* Check 4: zkSNARK Proof */}
      <CheckCard
        passed={analysis.zksnark_proof.passed}
        title="Доказательство zkSNARK"
        subtitle={analysis.zksnark_proof.nullifier_published ? 'Нуллификатор опубликован' : 'Без zkSNARK'}
      >
        <div style={{ color: '#aaa', marginBottom: 8 }}>{analysis.zksnark_proof.explanation}</div>
        <TxRow label="TX вывода" hash={analysis.zksnark_proof.withdraw_tx_hash} />
      </CheckCard>

      {/* Check 5: Time Separation */}
      <CheckCard
        passed={analysis.time_separation.passed}
        title="Временное разделение"
        subtitle={`Задержка: ${analysis.time_separation.delay_display}`}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <span style={{ color: '#888', fontSize: 12 }}>Депозит</span>
          <span style={{ color: '#aaa', fontSize: 12 }}>
            {analysis.time_separation.deposit_time
              ? new Date(analysis.time_separation.deposit_time).toLocaleString()
              : 'Н/Д'}
          </span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <span style={{ color: '#888', fontSize: 12 }}>Вывод</span>
          <span style={{ color: '#aaa', fontSize: 12 }}>
            {analysis.time_separation.withdraw_time
              ? new Date(analysis.time_separation.withdraw_time).toLocaleString()
              : 'Н/Д'}
          </span>
        </div>
        <div style={{ color: '#aaa', marginTop: 8 }}>
          Временная задержка между операциями затрудняет корреляцию транзакций.
        </div>
      </CheckCard>

      {/* Check 6: No On-Chain Link */}
      <CheckCard
        passed={analysis.no_onchain_link.passed}
        title="Отсутствие ончейн-связи"
        subtitle="Нет прослеживаемого пути"
      >
        <div style={{ color: '#aaa', marginBottom: 10 }}>{analysis.no_onchain_link.summary}</div>
        <TxRow label="Оплата" hash={analysis.no_onchain_link.user_tx_hash} />
        <TxRow label="Депозит" hash={analysis.no_onchain_link.deposit_tx_hash} />
        <TxRow label="Вывод" hash={analysis.no_onchain_link.withdraw_tx_hash} />
      </CheckCard>

      {/* Multi-unit details */}
      {analysis.unit_analyses && analysis.unit_analyses.length > 1 && (
        <CheckCard passed={true} title="Детали по единицам" subtitle={`${analysis.units} единиц обработано`}>
          {analysis.unit_analyses.map((u) => (
            <div key={u.unit_index} style={{
              marginBottom: 8, padding: 8, background: '#0f0f1a', borderRadius: 8,
            }}>
              <div style={{ color: '#fff', fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
                Единица {u.unit_index}
              </div>
              <TxRow label="Депозит" hash={u.deposit_tx_hash} />
              <TxRow label="Вывод" hash={u.withdraw_tx_hash} />
            </div>
          ))}
        </CheckCard>
      )}
    </div>
  )
}
