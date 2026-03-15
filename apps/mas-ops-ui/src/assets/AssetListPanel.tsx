import type { AssetResponse } from "../api/client";

type AssetListPanelProps = {
  assets: Array<AssetResponse>;
  selectedAssetId: string | null;
  onSelect(assetId: string): void;
};

export function AssetListPanel({
  assets,
  selectedAssetId,
  onSelect,
}: AssetListPanelProps) {
  if (assets.length === 0) {
    return (
      <p className="muted-copy">No assets are currently projected for this client.</p>
    );
  }

  return (
    <div className="list">
      {assets.map((asset) => (
        <button
          className={`list-item selectable ${selectedAssetId === asset.asset_id ? "selected" : ""}`}
          key={asset.asset_id}
          onClick={() => {
            onSelect(asset.asset_id);
          }}
          type="button"
        >
          <strong>{asset.hostname ?? asset.asset_id}</strong>
          <span>
            {asset.asset_kind} | {asset.health_state}
          </span>
        </button>
      ))}
    </div>
  );
}
