export interface Bidder {
  id: string;
  name: string;
  valuation: number;
  budget: number;
}
export interface RunConfig {
  item_name: string;
  reserve_price: number;
  bidders: Bidder[];
}
export type RunStatus = 'created' | 'collecting' | 'closed' | 'settled';
export interface Outcome {
  bidder_id: string;
  valuation: number;
  bid: number | null;
  payment: number;
  utility: number;
  won: boolean;
}
export interface Run extends RunConfig {
  id: string;
  mechanism: 'first_price_sealed_bid';
  status: RunStatus;
  round: number;
  created_at: string;
  updated_at: string;
  responded: string[];
  bids: Record<string, number> | null;
  results: null | {
    winner_id: string | null;
    winning_bid: number | null;
    revenue: number;
    outcomes: Outcome[];
  };
  event_count: number;
}
export type RunSummary = Pick<Run, 'id' | 'item_name' | 'status' | 'created_at'>;
export interface AuctionEvent {
  seq: number;
  type: string;
  actor: string;
  round: number;
  visibility: 'public' | 'private';
  payload: Record<string, unknown>;
  created_at: string;
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((x: { msg: string }) => x.msg).join('; ')
          : `Request failed (${response.status}).`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const credits = (value: number) => new Intl.NumberFormat('en-US').format(value);
export const humanize = (value: string) =>
  value.replaceAll('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase());
