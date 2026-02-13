import { useState, useCallback } from 'react'
import type { NetworkMode } from '../types'

const STORAGE_KEY = 'miximus_network_mode'

export function useNetworkMode() {
  const [networkMode, setNetworkModeState] = useState<NetworkMode>(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    return (stored === 'testnet' ? 'testnet' : 'mainnet') as NetworkMode
  })

  const setNetworkMode = useCallback((mode: NetworkMode) => {
    localStorage.setItem(STORAGE_KEY, mode)
    setNetworkModeState(mode)
  }, [])

  const toggleNetworkMode = useCallback(() => {
    setNetworkMode(networkMode === 'mainnet' ? 'testnet' : 'mainnet')
  }, [networkMode, setNetworkMode])

  return { networkMode, setNetworkMode, toggleNetworkMode }
}
