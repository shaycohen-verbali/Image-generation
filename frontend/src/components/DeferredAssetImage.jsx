import React, { useEffect, useState } from 'react'
import { buildAssetContentUrl } from '../lib/api'

export default function DeferredAssetImage({
  asset,
  alt,
  autoLoad = false,
  buttonLabel = 'Load preview',
  className = 'asset-image',
}) {
  const assetId = asset?.id || ''
  const [enabled, setEnabled] = useState(autoLoad)

  useEffect(() => {
    setEnabled(autoLoad)
  }, [assetId, autoLoad])

  if (!assetId) {
    return <p className="asset-meta-empty">Image URL unavailable.</p>
  }

  if (!enabled) {
    return (
      <div className="deferred-asset-placeholder">
        <button type="button" onClick={() => setEnabled(true)}>
          {buttonLabel}
        </button>
      </div>
    )
  }

  return (
    <img
      className={className}
      src={buildAssetContentUrl(asset)}
      alt={alt}
      loading="lazy"
      decoding="async"
    />
  )
}
