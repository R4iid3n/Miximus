import axios from 'axios'
import type {
  Pool, MixOrder, Asset, Chain, NetworkMode, PrivacyAnalysisData,
  AdminStats, AdminPool, AdminOrdersResponse, SeedJob, AdminBalances,
  WalletAddresses, WalletUpdateResult, InitPoolsResult, FeeWallets,
} from '../types'

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// Pools
export async function listPools(networkMode: NetworkMode): Promise<{ pools: Pool[], network_mode: string }> {
  const { data } = await api.get('/pools', { params: { network_mode: networkMode } })
  return data
}

// Assets
export async function listAssets(networkMode: NetworkMode): Promise<{ assets: Asset[], chains: Chain[] }> {
  const { data } = await api.get('/assets', { params: { network_mode: networkMode } })
  return data
}

// Orders
export async function createOrder(params: {
  symbol: string, chain: string, recipient_address: string, network_mode: NetworkMode, units: number
}): Promise<MixOrder> {
  const { data } = await api.post('/order/create', params)
  return data
}

export async function submitTxHash(orderId: string, txHash: string): Promise<MixOrder> {
  const { data } = await api.post('/order/submit-tx', { order_id: orderId, tx_hash: txHash })
  return data
}

export async function getOrderStatus(orderId: string): Promise<MixOrder> {
  const { data } = await api.get(`/order/${orderId}/status`)
  return data
}

export async function getOrderAnalysis(orderId: string): Promise<PrivacyAnalysisData> {
  const { data } = await api.get(`/order/${orderId}/analysis`)
  return data
}

// ── Admin (all require a valid JWT token) ────────────────────────────────────

function adminHeaders(token: string) {
  return { Authorization: `Bearer ${token}` }
}

export async function adminLogin(username: string, password: string): Promise<{ token: string }> {
  const { data } = await api.post('/admin/login', { username, password })
  return data
}

export async function adminGetStats(token: string): Promise<AdminStats> {
  const { data } = await api.get('/admin/stats', { headers: adminHeaders(token) })
  return data
}

export async function adminGetOrders(
  token: string,
  filters: { status?: string; symbol?: string; chain?: string; network_mode?: string; limit?: number; offset?: number }
): Promise<AdminOrdersResponse> {
  const { data } = await api.get('/admin/orders', {
    headers: adminHeaders(token),
    params: filters,
  })
  return data
}

export async function adminGetPools(token: string): Promise<{ pools: AdminPool[] }> {
  const { data } = await api.get('/admin/pools', { headers: adminHeaders(token) })
  return data
}

export async function adminSeedPool(
  token: string,
  params: { symbol: string; chain: string; network_mode: string; units: number }
): Promise<{ job_id: string }> {
  const { data } = await api.post('/admin/seed', params, { headers: adminHeaders(token) })
  return data
}

export async function adminGetSeedStatus(token: string, jobId: string): Promise<SeedJob> {
  const { data } = await api.get(`/admin/seed-status/${jobId}`, { headers: adminHeaders(token) })
  return data
}

export async function adminGetBalances(token: string): Promise<AdminBalances> {
  const { data } = await api.get('/admin/balances', { headers: adminHeaders(token) })
  return data
}

export async function adminUpdatePool(
  token: string,
  poolId: number,
  patch: { service_wallet_address?: string; enabled?: boolean },
): Promise<AdminPool> {
  const { data } = await api.patch(`/admin/pools/${poolId}`, patch, { headers: adminHeaders(token) })
  return data
}

export async function adminInitPools(token: string): Promise<InitPoolsResult> {
  const { data } = await api.post('/admin/init-pools', {}, { headers: adminHeaders(token) })
  return data
}

export async function adminGetWallet(token: string): Promise<WalletAddresses> {
  const { data } = await api.get('/admin/wallet', { headers: adminHeaders(token) })
  return data
}

export async function adminUpdateWallet(
  token: string,
  privateKey: string,
): Promise<WalletUpdateResult> {
  const { data } = await api.post(
    '/admin/wallet',
    { private_key: privateKey },
    { headers: adminHeaders(token) },
  )
  return data
}

export async function adminGetFeeWallets(token: string): Promise<FeeWallets> {
  const { data } = await api.get('/admin/fee-wallets', { headers: adminHeaders(token) })
  return data
}

export async function adminUpdateFeeWallets(
  token: string,
  wallets: FeeWallets,
): Promise<FeeWallets & { success: boolean; note: string; live_updated: boolean }> {
  const { data } = await api.post('/admin/fee-wallets', wallets, { headers: adminHeaders(token) })
  return data
}

export default api
