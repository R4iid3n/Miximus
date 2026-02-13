import { useState, useEffect, useCallback } from 'react'
import type { Pool, NetworkMode } from '../types'
import { listPools } from '../services/api'

export function usePools(networkMode: NetworkMode) {
  const [pools, setPools] = useState<Pool[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchPools = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await listPools(networkMode)
      setPools(result.pools || [])
    } catch (e: any) {
      setError(e.response?.data?.error || e.message)
      setPools([])
    } finally {
      setLoading(false)
    }
  }, [networkMode])

  useEffect(() => { fetchPools() }, [fetchPools])

  return { pools, loading, error, refresh: fetchPools }
}
