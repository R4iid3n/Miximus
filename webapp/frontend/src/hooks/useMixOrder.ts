import { useState, useEffect, useCallback, useRef } from 'react'
import type { MixOrder, NetworkMode } from '../types'
import { createOrder, submitTxHash, getOrderStatus } from '../services/api'

export function useMixOrder() {
  const [order, setOrder] = useState<MixOrder | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Create a new mix order
  const create = useCallback(async (
    symbol: string, chain: string, recipientAddress: string, networkMode: NetworkMode, units: number = 1
  ) => {
    setLoading(true)
    setError(null)
    try {
      const result = await createOrder({ symbol, chain, recipient_address: recipientAddress, network_mode: networkMode, units })
      setOrder(result)
      return result
    } catch (e: any) {
      const msg = e.response?.data?.error || e.message
      setError(msg)
      throw new Error(msg)
    } finally {
      setLoading(false)
    }
  }, [])

  // Submit user's payment TX hash
  const submitTx = useCallback(async (txHash: string) => {
    if (!order) return
    setLoading(true)
    setError(null)
    try {
      const result = await submitTxHash(order.order_id, txHash)
      setOrder(result)
      return result
    } catch (e: any) {
      setError(e.response?.data?.error || e.message)
    } finally {
      setLoading(false)
    }
  }, [order])

  // Poll order status
  const startPolling = useCallback((orderId: string) => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const result = await getOrderStatus(orderId)
        setOrder(result)
        // Stop polling on terminal states
        if (['completed', 'failed', 'expired'].includes(result.status)) {
          if (pollRef.current) clearInterval(pollRef.current)
          pollRef.current = null
        }
      } catch { /* ignore polling errors */ }
    }, 5000)
  }, [])

  // Load existing order by ID
  const loadOrder = useCallback(async (orderId: string) => {
    setLoading(true)
    setError(null)
    try {
      const result = await getOrderStatus(orderId)
      setOrder(result)
      // Start polling if not in terminal state
      if (!['completed', 'failed', 'expired'].includes(result.status)) {
        startPolling(orderId)
      }
      return result
    } catch (e: any) {
      setError(e.response?.data?.error || e.message)
    } finally {
      setLoading(false)
    }
  }, [startPolling])

  const reset = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = null
    setOrder(null)
    setError(null)
  }, [])

  // Cleanup on unmount
  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  return { order, loading, error, create, submitTx, startPolling, loadOrder, reset }
}
