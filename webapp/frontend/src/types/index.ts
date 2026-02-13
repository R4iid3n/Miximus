export type NetworkMode = 'mainnet' | 'testnet'

export type OrderStatus =
  | 'pending_payment' | 'payment_detected' | 'payment_confirmed'
  | 'depositing' | 'deposited' | 'proving' | 'withdrawing'
  | 'completed' | 'failed' | 'expired'

export interface Pool {
  symbol: string
  chain: string
  denomination: string
  denomination_display: string
  commission_rate: number
  payout_per_unit?: string
  payout_display: string
  service_address: string
  mixer_contract: string
  pool_balance?: string
  available_units: number
  enabled: boolean
}

export interface OrderStep {
  name: string
  status: 'pending' | 'in_progress' | 'completed' | 'failed'
  tx_hash?: string | null
}

export interface MixOrder {
  order_id: string
  symbol: string
  chain: string
  network_mode: NetworkMode
  recipient_address: string
  service_address: string
  denomination: string
  denomination_display?: string
  units: number
  total_amount: string
  total_amount_display?: string
  commission_rate: number
  payout_display?: string
  commission_amount?: string
  payout_amount?: string
  completed_units: number
  current_unit: number
  user_tx_hash?: string | null
  deposit_tx_hash?: string | null
  withdraw_tx_hash?: string | null
  status: OrderStatus
  error_message?: string | null
  created_at: string
  expires_at: string
  withdrawn_at?: string | null
  steps?: OrderStep[]
}

export interface Chain {
  chain_id: number | string | null
  name: string
  type: string
  native_currency: string
  native_decimals: number
  rpc_url: string
  explorer: string
}

export interface Asset {
  symbol: string
  name: string
  chain: string
  type: string
  decimals: number
  denomination: string
  contract?: string
  mixer_contract?: string
}
