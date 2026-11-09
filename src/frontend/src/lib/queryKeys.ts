/**
 * Central React Query key factory.
 *
 * The same two endpoints were previously cached under six different keys
 * (`agents`, `agents-search`, `agent-configs`, `evaluations`,
 * `['evaluations', page]`, `evaluations-search`), so a single dashboard load
 * refetched identical data three times and no component shared a cache entry.
 */
export const queryKeys = {
  agents: {
    all: ['agents'] as const,
    list: () => ['agents', 'list'] as const,
    detail: (id: string) => ['agents', 'detail', id] as const,
  },
  evaluations: {
    all: ['evaluations'] as const,
    list: (page: number, limit: number) =>
      ['evaluations', 'list', { page, limit }] as const,
    detail: (id: string) => ['evaluations', 'detail', id] as const,
    results: (id: string) => ['evaluations', 'results', id] as const,
  },
  operations: {
    summary: () => ['operations', 'summary'] as const,
    drift: (limit: number) => ['operations', 'drift', { limit }] as const,
  },
} as const;

/** Statuses for which polling should continue. */
export const ACTIVE_STATUSES = ['pending', 'running'];

export const isActiveStatus = (status?: string | null): boolean =>
  !!status && ACTIVE_STATUSES.includes(status);
