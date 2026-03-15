import type {
  ActivityEventResponse,
  AssetResponse,
  ClientSummaryResponse,
  IncidentResponse,
} from "../api/client";

export type PortfolioState = {
  clients: Array<ClientSummaryResponse>;
  errorMessage: string | null;
  status: "loading" | "ready" | "error";
};

export type PortfolioAction =
  | { type: "loading" }
  | { type: "loaded"; clients: Array<ClientSummaryResponse> }
  | { type: "client_replaced"; client: ClientSummaryResponse }
  | { type: "failed"; message: string };

export const initialPortfolioState: PortfolioState = {
  clients: [],
  errorMessage: null,
  status: "loading",
};

export function portfolioReducer(
  state: PortfolioState,
  action: PortfolioAction,
): PortfolioState {
  switch (action.type) {
    case "loading":
      return { ...state, errorMessage: null, status: "loading" };
    case "loaded":
      return { clients: action.clients, errorMessage: null, status: "ready" };
    case "client_replaced": {
      const hasExistingClient = state.clients.some(
        (client) => client.client_id === action.client.client_id,
      );
      return {
        ...state,
        clients: hasExistingClient
          ? state.clients.map((client) =>
              client.client_id === action.client.client_id ? action.client : client,
            )
          : [...state.clients, action.client],
      };
    }
    case "failed":
      return {
        ...state,
        errorMessage: action.message,
        status: state.clients.length > 0 ? "ready" : "error",
      };
  }
}

export type ClientDetailState = {
  assets: Array<AssetResponse>;
  client: ClientSummaryResponse | null;
  clientActivity: Array<ActivityEventResponse>;
  errorMessage: string | null;
  incidents: Array<IncidentResponse>;
  selectedAsset: AssetResponse | null;
  selectedAssetActivity: Array<ActivityEventResponse>;
  selectedAssetId: string | null;
  status: "loading" | "ready" | "error";
};

export type ClientDetailAction =
  | {
      type: "loaded";
      assets: Array<AssetResponse>;
      client: ClientSummaryResponse;
      clientActivity: Array<ActivityEventResponse>;
      incidents: Array<IncidentResponse>;
      selectedAsset: AssetResponse | null;
      selectedAssetActivity: Array<ActivityEventResponse>;
      selectedAssetId: string | null;
    }
  | { type: "client_loaded"; client: ClientSummaryResponse }
  | { type: "client_activity_loaded"; activity: Array<ActivityEventResponse> }
  | { type: "activity_appended"; activity: ActivityEventResponse }
  | {
      type: "assets_loaded";
      assets: Array<AssetResponse>;
      selectedAsset: AssetResponse | null;
      selectedAssetId: string | null;
    }
  | { type: "asset_replaced"; asset: AssetResponse }
  | { type: "asset_selected"; assetId: string | null }
  | {
      type: "asset_loaded";
      activity: Array<ActivityEventResponse>;
      asset: AssetResponse | null;
    }
  | { type: "incidents_loaded"; incidents: Array<IncidentResponse> }
  | { type: "loading" }
  | { type: "failed"; message: string };

export const initialClientDetailState: ClientDetailState = {
  assets: [],
  client: null,
  clientActivity: [],
  errorMessage: null,
  incidents: [],
  selectedAsset: null,
  selectedAssetActivity: [],
  selectedAssetId: null,
  status: "loading",
};

export function clientDetailReducer(
  state: ClientDetailState,
  action: ClientDetailAction,
): ClientDetailState {
  switch (action.type) {
    case "loaded":
      return {
        assets: action.assets,
        client: action.client,
        clientActivity: action.clientActivity,
        errorMessage: null,
        incidents: action.incidents,
        selectedAsset: action.selectedAsset,
        selectedAssetActivity: action.selectedAssetActivity,
        selectedAssetId: action.selectedAssetId,
        status: "ready",
      };
    case "client_loaded":
      return { ...state, client: action.client, status: "ready" };
    case "client_activity_loaded":
      return { ...state, clientActivity: action.activity, status: "ready" };
    case "activity_appended":
      return {
        ...state,
        clientActivity: prependActivity(state.clientActivity, action.activity),
        selectedAssetActivity:
          action.activity.asset_id !== null &&
          action.activity.asset_id === state.selectedAssetId
            ? prependActivity(state.selectedAssetActivity, action.activity)
            : state.selectedAssetActivity,
        status: "ready",
      };
    case "assets_loaded":
      return {
        ...state,
        assets: action.assets,
        selectedAsset: action.selectedAsset,
        selectedAssetId: action.selectedAssetId,
        status: "ready",
      };
    case "asset_replaced": {
      const hasExistingAsset = state.assets.some(
        (asset) => asset.asset_id === action.asset.asset_id,
      );
      return {
        ...state,
        assets: hasExistingAsset
          ? state.assets.map((asset) =>
              asset.asset_id === action.asset.asset_id ? action.asset : asset,
            )
          : [...state.assets, action.asset],
        selectedAsset:
          state.selectedAssetId === action.asset.asset_id
            ? action.asset
            : state.selectedAsset,
        status: "ready",
      };
    }
    case "asset_selected":
      return {
        ...state,
        selectedAssetActivity: [],
        selectedAssetId: action.assetId,
      };
    case "asset_loaded":
      return {
        ...state,
        selectedAsset: action.asset,
        selectedAssetActivity: action.activity,
        status: "ready",
      };
    case "incidents_loaded":
      return { ...state, incidents: action.incidents, status: "ready" };
    case "loading":
      return { ...state, errorMessage: null, status: "loading" };
    case "failed":
      return {
        ...state,
        errorMessage: action.message,
        status:
          state.client !== null ||
          state.assets.length > 0 ||
          state.incidents.length > 0 ||
          state.clientActivity.length > 0
            ? "ready"
            : "error",
      };
  }
}

function prependActivity(
  existing: Array<ActivityEventResponse>,
  incoming: ActivityEventResponse,
): Array<ActivityEventResponse> {
  return [
    incoming,
    ...existing.filter((event) => event.activity_id !== incoming.activity_id),
  ];
}
