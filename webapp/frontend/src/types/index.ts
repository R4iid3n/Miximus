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

// ----- Admin -----

export interface AdminPoolSummary {
  symbol: string
  chain: string
  network_mode: string
  available: number
  reserved: number
  withdrawn: number
}

export interface AdminStats {
  orders_by_status: Record<string, number>
  total_orders: number
  pools: AdminPoolSummary[]
}

export interface AdminPool {
  id: number
  symbol: string
  chain: string
  network_mode: string
  mixer_contract: string
  denomination: string
  commission_rate: number
  service_wallet_address: string
  enabled: boolean
  min_confirmations: number
  available: number
  reserved: number
  withdrawn: number
}

export interface SeedJob {
  job_id: string
  total: number
  done: number
  failed: number
  running: boolean
  errors: string[]
}

export interface AdminBalances {
  evm: { address: string; balance_matic: string }
  btc_mainnet: { address: string; balance_btc: string }
  btc_testnet: { address: string; balance_btc: string }
}

export interface WalletAddresses {
  evm_address: string
  btc_mainnet_address: string
  btc_testnet_address: string
  tron_address: string
}

export interface WalletUpdateResult {
  success: boolean
  addresses: WalletAddresses
  pools_updated: number
  restart_required: boolean
  note: string
}

export interface InitPoolsResult {
  created: number
  updated: number
  total: number
}

export interface FeeWalletSet {
  evm: string
  tron: string
  btc: string
}

export interface FeeWallets {
  mainnet: FeeWalletSet
  testnet: FeeWalletSet
}

export interface AdminOrdersResponse {
  orders: MixOrder[]
  total: number
  limit: number
  offset: number
}

// ----- Privacy Analysis -----

export interface AddressSeparationCheck {
  passed: boolean
  sender_address: string | null
  service_address: string
  mixer_contract: string
  recipient_address: string
  all_different: boolean
  chain_type: string
}

export interface AnonymitySetCheck {
  passed: boolean
  total_deposits: number
  pool_description: string
}

export interface DenominationUniformityCheck {
  passed: boolean
  denomination_display: string
  explanation: string
}

export interface ZkSnarkProofCheck {
  passed: boolean
  nullifier_published: boolean
  withdraw_tx_hash: string | null
  explanation: string
}

export interface TimeSeparationCheck {
  passed: boolean
  deposit_time: string | null
  withdraw_time: string | null
  delay_seconds: number | null
  delay_display: string
}

export interface NoOnchainLinkCheck {
  passed: boolean
  user_tx_hash: string | null
  deposit_tx_hash: string | null
  withdraw_tx_hash: string | null
  summary: string
}

export interface UnitAnalysis {
  unit_index: number
  deposit_tx_hash: string | null
  withdraw_tx_hash: string | null
}

export interface PrivacyAnalysisData {
  order_id: string
  chain: string
  symbol: string
  denomination_display: string
  address_separation: AddressSeparationCheck
  anonymity_set: AnonymitySetCheck
  denomination_uniformity: DenominationUniformityCheck
  zksnark_proof: ZkSnarkProofCheck
  time_separation: TimeSeparationCheck
  no_onchain_link: NoOnchainLinkCheck
  units: number
  unit_analyses: UnitAnalysis[] | null
  total_checks: number
  passed_checks: number
  overall_passed: boolean
}
