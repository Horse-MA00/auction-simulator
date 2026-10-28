import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { AuctionEvent, Run, Session } from './api';

const admin: Session = {
  user: { id: 'admin-1', username: 'researcher', role: 'admin' },
  csrf_token: 'admin-csrf',
};
const alice: Session = {
  user: { id: 'user-1', username: 'alice', role: 'user' },
  csrf_token: 'alice-csrf',
};
const created: Run = {
  id: 'run-test',
  item_name: 'GPU time allocation',
  reserve_price: 400,
  mechanism: 'first_price_sealed_bid',
  status: 'created',
  round: 1,
  created_at: '2026-10-28T09:00:00Z',
  updated_at: '2026-10-28T09:00:00Z',
  bidders: [
    { id: 'bidder_1', name: 'Aster', valuation: 1000, budget: 1000, owner_user_id: 'user-1' },
    { id: 'bidder_2', name: 'Birch', valuation: 900, budget: 1000, owner_user_id: 'user-2' },
    { id: 'bidder_3', name: 'Cedar', valuation: 1200, budget: 1500, owner_user_id: null },
  ],
  responded: [],
  bids: {},
  results: null,
  event_count: 1,
};
const settled: Run = {
  ...created,
  status: 'settled',
  event_count: 7,
  responded: ['bidder_1', 'bidder_2', 'bidder_3'],
  bids: { bidder_1: 800, bidder_2: 720, bidder_3: 960 },
  results: {
    winner_id: 'bidder_3',
    winning_bid: 960,
    revenue: 960,
    outcomes: [
      { bidder_id: 'bidder_1', valuation: 1000, bid: 800, payment: 0, utility: 0, won: false },
      { bidder_id: 'bidder_2', valuation: 900, bid: 720, payment: 0, utility: 0, won: false },
      { bidder_id: 'bidder_3', valuation: 1200, bid: 960, payment: 960, utility: 240, won: true },
    ],
  },
};
const userRun: Run = {
  ...settled,
  bidders: settled.bidders.map((bidder) =>
    bidder.owner_user_id === alice.user.id ? bidder : { ...bidder, valuation: null, budget: null },
  ),
  bids: { bidder_1: 800 },
  results: {
    ...settled.results!,
    revenue: null,
    winning_bid: null,
    outcomes: [settled.results!.outcomes[0]],
  },
};
const events: AuctionEvent[] = Array.from({ length: 7 }, (_, i) => ({
  seq: i + 1,
  type: i === 6 ? 'auction_settled' : 'message_received',
  actor: 'auctioneer',
  round: 1,
  visibility: 'public',
  payload: {},
  created_at: created.created_at,
}));
const respond = (data: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }),
  );

function mockFetch(
  handler: (url: string, options?: RequestInit) => Promise<Response>,
  session: Session | null = admin,
) {
  const mock = vi.fn((url: string, options?: RequestInit) => {
    if (url === '/api/auth/me')
      return session ? respond(session) : respond({ detail: 'Sign in required.' }, 401);
    if (url === '/api/admin/users') return respond({ users: [admin.user, alice.user] });
    return handler(url, options);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}
function runRoutes(run: Run) {
  return (url: string) => {
    if (url === '/api/runs') return respond({ runs: [run] });
    if (url.endsWith('/events')) return respond({ events });
    if (url.endsWith('/replay?through=1')) return respond(created);
    if (url === '/api/runs/run-test') return respond(run);
    return respond({ detail: 'Not found' }, 404);
  };
}
class FakeSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  close() {}
}
beforeEach(() => {
  vi.stubGlobal('WebSocket', FakeSocket);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('administrator workbench', () => {
  it('creates an assigned auction and includes the session CSRF token', async () => {
    const fetchMock = mockFetch((url, options) => {
      if (url === '/api/runs' && options?.method === 'POST') return respond(created, 201);
      if (url === '/api/runs') return respond({ runs: [] });
      if (url.endsWith('/events')) return respond({ events: [events[0]] });
      if (url === '/api/runs/run-test') return respond(created);
      return respond({}, 404);
    });
    render(<App />);
    await screen.findByRole('button', { name: 'Create auction →' });
    await within(screen.getByRole('combobox', { name: 'Bidder 1 account' })).findByRole('option', {
      name: 'alice',
    });
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Bidder 1 account' }),
      'user-1',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Create auction →' }));
    await screen.findByText('Ready to open');
    const call = fetchMock.mock.calls.find(
      ([url, options]) => url === '/api/runs' && options?.method === 'POST',
    );
    expect(JSON.parse(call?.[1]?.body as string).bidders[0]).toMatchObject({
      id: 'bidder_1',
      owner_user_id: 'user-1',
    });
    expect(call?.[1]?.headers).toMatchObject({ 'X-CSRF-Token': 'admin-csrf' });
    expect(screen.getAllByText('Waiting')).toHaveLength(3);
    expect(screen.getByRole('button', { name: 'Step once →' })).toBeEnabled();
  });

  it('shows every submitted bid to the administrator before settlement', async () => {
    const collecting: Run = {
      ...created,
      status: 'collecting',
      responded: ['bidder_1', 'bidder_2'],
      bids: { bidder_1: 800, bidder_2: 720 },
      event_count: 4,
    };
    mockFetch(runRoutes(collecting));
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /GPU time allocation/ }));
    expect(await screen.findByText('800')).toBeInTheDocument();
    expect(screen.getByText('720')).toBeInTheDocument();
    expect(screen.getByText('Administrator view:', { exact: false })).toBeInTheDocument();
  });

  it('replays historical state without leaking the final allocation', async () => {
    mockFetch(runRoutes(settled));
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /GPU time allocation/ }));
    expect(await screen.findByText('WINNER')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('slider'), { target: { value: '1' } });
    await waitFor(() => expect(screen.queryByText('WINNER')).not.toBeInTheDocument());
    expect(screen.getByText('REPLAY SNAPSHOT')).toBeInTheDocument();
    await screen.findAllByText('Waiting');
    await userEvent.click(screen.getByRole('button', { name: 'Return to final result' }));
    expect(await screen.findByText('WINNER')).toBeInTheDocument();
  });

  it('shows a validation failure and lets the administrator correct the configuration', async () => {
    mockFetch((url, options) =>
      url === '/api/runs' && options?.method === 'POST'
        ? respond({ detail: 'Bidder names must be unique.' }, 422)
        : respond({ runs: [] }),
    );
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: 'Create auction →' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Bidder names must be unique.');
    expect(screen.getByRole('button', { name: 'Create auction →' })).toBeEnabled();
  });

  it('clears a longer event history when switching to a newly created run', async () => {
    const fresh = { ...created, id: 'fresh-run', item_name: 'New experiment item' };
    mockFetch((url) => {
      if (url === '/api/runs') return respond({ runs: [settled, fresh] });
      if (url === '/api/runs/fresh-run/events') return respond({ events: [events[0]] });
      if (url === '/api/runs/fresh-run') return respond(fresh);
      return runRoutes(settled)(url);
    });
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /GPU time allocation/ }));
    await screen.findByText('7 events');
    await userEvent.click(screen.getByRole('button', { name: /New experiment item/ }));
    expect(await screen.findByText('1 events')).toBeInTheDocument();
    expect(screen.queryByText('Auction Settled')).not.toBeInTheDocument();
  });

  it('hides final results while an earlier replay snapshot is loading', async () => {
    let resolveReplay!: (response: Response) => void;
    mockFetch((url) =>
      url.endsWith('/replay?through=1')
        ? new Promise<Response>((resolve) => {
            resolveReplay = resolve;
          })
        : runRoutes(settled)(url),
    );
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /GPU time allocation/ }));
    await screen.findByText('WINNER');
    fireEvent.change(screen.getByRole('slider'), { target: { value: '1' } });
    expect(screen.getByRole('status')).toHaveTextContent('Loading state at event 1');
    expect(screen.queryByText('WINNER')).not.toBeInTheDocument();
    resolveReplay(new Response(JSON.stringify(created), { status: 200 }));
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
    expect(screen.getAllByText('Waiting')).toHaveLength(3);
  });
});

describe('participant permissions', () => {
  it('shows only own private amounts and no administrative controls', async () => {
    mockFetch(runRoutes(userRun), alice);
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /GPU time allocation/ }));
    const table = await screen.findByRole('table');
    expect(within(table).getByText('800')).toBeInTheDocument();
    expect(within(table).queryByText('720')).not.toBeInTheDocument();
    expect(within(table).queryByText('960')).not.toBeInTheDocument();
    expect(within(table).queryByText('1,200')).not.toBeInTheDocument();
    expect(within(table).getAllByText('Private').length).toBeGreaterThan(4);
    expect(screen.queryByRole('button', { name: /New experiment/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Step once/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Auto-play/ })).not.toBeInTheDocument();
    expect(screen.getByText('Your surplus')).toBeInTheDocument();
  });

  it('shows own submitted bid during collection without inferring opponents’ bids', async () => {
    const collecting: Run = { ...userRun, status: 'collecting', results: null, event_count: 4 };
    mockFetch(runRoutes(collecting), alice);
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /GPU time allocation/ }));
    expect(await screen.findByText('800')).toBeInTheDocument();
    expect(screen.getAllByText('Private').length).toBeGreaterThan(4);
  });
});

describe('account sessions', () => {
  it('registers a regular account and opens its empty assigned workspace', async () => {
    const fetchMock = mockFetch((url, options) => {
      if (url === '/api/auth/register' && options?.method === 'POST') return respond(alice, 201);
      return respond({ runs: [] });
    }, null);
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: 'Create an account' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Username' }), 'alice');
    await userEvent.type(screen.getByLabelText('Password'), 'short');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByText('No assigned auctions yet')).toBeInTheDocument();
    expect(screen.getByText('Participant')).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([url]) => url === '/api/auth/register');
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({
      username: 'alice',
      password: 'short',
    });
  });

  it('logs in and restores the role from the server response', async () => {
    mockFetch((url) => (url === '/api/auth/login' ? respond(admin) : respond({ runs: [] })), null);
    render(<App />);
    await userEvent.type(await screen.findByRole('textbox', { name: 'Username' }), 'researcher');
    await userEvent.type(screen.getByLabelText('Password'), 'administrator-password');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('button', { name: 'Create auction →' })).toBeInTheDocument();
    expect(screen.getByText('Administrator')).toBeInTheDocument();
  });

  it('clears cached auction data on logout and sends a CSRF-protected request', async () => {
    const fetchMock = mockFetch((url) =>
      url === '/api/auth/logout' ? respond({ ok: true }) : runRoutes(settled)(url),
    );
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /GPU time allocation/ }));
    await screen.findByText('WINNER');
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByText('GPU time allocation')).not.toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([url]) => url === '/api/auth/logout');
    expect(call?.[1]?.headers).toMatchObject({ 'X-CSRF-Token': 'admin-csrf' });
  });

  it('clears the workspace after an authenticated request returns 401', async () => {
    mockFetch((url) =>
      url === '/api/runs'
        ? respond({ runs: [settled] })
        : respond({ detail: 'Session expired.' }, 401),
    );
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /GPU time allocation/ }));
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Your session has expired.');
    expect(screen.queryByText('GPU time allocation')).not.toBeInTheDocument();
  });
});
