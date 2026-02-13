import axios from 'axios'
import type { Pool, MixOrder, Asset, Chain, NetworkMode } from '../types'

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

export default api
