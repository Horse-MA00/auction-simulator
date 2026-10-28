import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { AuctionEvent, Run } from './api';

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
    { id: 'bidder_1', name: 'Aster', valuation: 1000, budget: 1000 },
    { id: 'bidder_2', name: 'Birch', valuation: 900, budget: 1000 },
    { id: 'bidder_3', name: 'Cedar', valuation: 1200, budget: 1500 },
  ],
  responded: [],
  bids: null,
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

describe('experiment workbench', () => {
  it('creates a configured run without exposing a bid before settlement', async () => {
    const fetchMock = vi.fn((url: string, options?: RequestInit) => {
      if (url === '/api/runs' && options?.method === 'POST') return respond(created, 201);
      if (url === '/api/runs') return respond({ runs: [] });
      if (url.endsWith('/events')) return respond({ events: [events[0]] });
      if (url === '/api/runs/run-test') return respond(created);
      return respond({ detail: 'Not found' }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: 'Create auction →' }));
    await screen.findByText('Ready to open');
    const call = fetchMock.mock.calls.find(([, options]) => options?.method === 'POST');
    expect(JSON.parse(call?.[1]?.body as string)).toMatchObject({
      item_name: 'GPU time allocation',
      reserve_price: 400,
      bidders: created.bidders,
    });
    expect(screen.getAllByText('Waiting')).toHaveLength(3);
    expect(screen.queryByText('960')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Step once →' })).toBeEnabled();
  });

  it('replays historical state without leaking the final allocation', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === '/api/runs') return respond({ runs: [settled] });
      if (url.endsWith('/events')) return respond({ events });
      if (url.endsWith('/replay?through=1')) return respond(created);
      if (url === '/api/runs/run-test') return respond(settled);
      return respond({ detail: 'Not found' }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /GPU time allocation/ }));
    expect(await screen.findByText('WINNER')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('slider'), { target: { value: '1' } });
    await waitFor(() => expect(screen.queryByText('WINNER')).not.toBeInTheDocument());
    expect(screen.getByText('REPLAY SNAPSHOT')).toBeInTheDocument();
    expect(screen.getAllByText('Waiting')).toHaveLength(3);
    await userEvent.click(screen.getByRole('button', { name: 'Return to final result' }));
    expect(await screen.findByText('WINNER')).toBeInTheDocument();
  });

  it('shows a validation failure and lets the user correct the configuration', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, options?: RequestInit) => {
        if (url === '/api/runs' && options?.method === 'POST')
          return respond({ detail: 'Bidder names must be unique.' }, 422);
        return respond({ runs: [] });
      }),
    );
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: 'Create auction →' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Bidder names must be unique.');
    expect(screen.getByRole('button', { name: 'Create auction →' })).toBeEnabled();
    expect(screen.getByRole('textbox', { name: 'Bidder 1 name' })).toBeEnabled();
  });

  it('clears a longer event history when switching to a newly created run', async () => {
    const fresh = { ...created, id: 'fresh-run', item_name: 'New experiment item' };
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        if (url === '/api/runs') return respond({ runs: [settled, fresh] });
        if (url === '/api/runs/run-test/events') return respond({ events });
        if (url === '/api/runs/run-test') return respond(settled);
        if (url === '/api/runs/fresh-run/events') return respond({ events: [events[0]] });
        if (url === '/api/runs/fresh-run') return respond(fresh);
        return respond({}, 404);
      }),
    );
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /GPU time allocation/ }));
    await screen.findByText('7 events');
    await userEvent.click(screen.getByRole('button', { name: /New experiment item/ }));
    expect(await screen.findByText('1 events')).toBeInTheDocument();
    expect(screen.queryByText('Auction Settled')).not.toBeInTheDocument();
  });

  it('does not display final results while an earlier replay snapshot is loading', async () => {
    let resolveReplay!: (response: Response) => void;
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        if (url === '/api/runs') return respond({ runs: [settled] });
        if (url.endsWith('/events')) return respond({ events });
        if (url.endsWith('/replay?through=1'))
          return new Promise<Response>((resolve) => {
            resolveReplay = resolve;
          });
        if (url === '/api/runs/run-test') return respond(settled);
        return respond({}, 404);
      }),
    );
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /GPU time allocation/ }));
    await screen.findByText('WINNER');
    fireEvent.change(screen.getByRole('slider'), { target: { value: '1' } });
    expect(screen.getByRole('status')).toHaveTextContent('Loading state at event 1');
    expect(screen.queryByText('WINNER')).not.toBeInTheDocument();
    expect(screen.queryByText('Winner surplus')).not.toBeInTheDocument();
    resolveReplay(new Response(JSON.stringify(created), { status: 200 }));
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
    expect(screen.getAllByText('Waiting')).toHaveLength(3);
  });
});
