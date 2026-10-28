export interface Bidder {
  id: string;
  name: string;
  valuation: number;
  budget: number;
  owner_user_id?: string | null;
}
export interface User {
  id: string;
  username: string;
  role: 'admin' | 'user';
}
export interface Session {
  user: User;
  csrf_token: string;
}
export type BidderView = Omit<Bidder, 'valuation' | 'budget'> & {
  valuation: number | null;
  budget: number | null;
};
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
export interface Run extends Omit<RunConfig, 'bidders'> {
  bidders: BidderView[];
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
    revenue: number | null;
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

let csrfToken = '';
export const setCsrfToken = (token: string) => {
  csrfToken = token;
};
export const SESSION_EXPIRED = 'auction:session-expired';
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const method = options?.method?.toUpperCase() ?? 'GET';
  const response = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrfToken
        ? { 'X-CSRF-Token': csrfToken }
        : {}),
      ...options?.headers,
    },
  });
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/auth/'))
      window.dispatchEvent(new Event(SESSION_EXPIRED));
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((x: { msg: string }) => x.msg).join('; ')
          : `Request failed (${response.status}).`;
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}

export const credits = (value: number | null) =>
  value === null ? 'Private' : new Intl.NumberFormat('en-US').format(value);
export const humanize = (value: string) =>
  value.replaceAll('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase());
