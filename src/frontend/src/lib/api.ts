import axios from 'axios';

const isServer = typeof window === 'undefined';

/**
 * Base URL for API calls.
 *
 * NEXT_PUBLIC_* values are inlined by Next.js at BUILD time, so this must be
 * supplied as a Docker build arg — setting it as a runtime environment variable
 * has no effect. When the frontend and backend are served through the same nginx
 * origin, leave it empty so relative (same-origin) URLs are used.
 */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || (isServer ? 'http://backend:8000' : '');

export const api = axios.create({
  baseURL: API_URL,
  withCredentials: true, // Crucial for sending/receiving the HttpOnly refresh token
});

/**
 * Absolute URL for EventSource connections.
 *
 * EventSource cannot carry an Authorization header, so the stream is
 * ticket-authenticated. It also does not respect axios' baseURL, so building the
 * URL here keeps SSE pointed at the same origin as every other API call — the
 * previous relative-only URL broke as soon as NEXT_PUBLIC_API_URL was set.
 */
export const apiUrl = (path: string): string => `${API_URL}${path}`;

// We keep the access token strictly in memory
let accessToken: string | null = null;

export const setAccessToken = (token: string | null) => {
  accessToken = token;
};

export const getAccessToken = () => accessToken;

// Request interceptor to attach the access token
api.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

/**
 * In-flight refresh, shared across concurrent 401s.
 *
 * Without this, N simultaneous requests failing with 401 each fired their own
 * /auth/refresh call. Beyond being wasteful, that races the auth rate limiter.
 */
let refreshPromise: Promise<string> | null = null;

const refreshAccessToken = (): Promise<string> => {
  if (!refreshPromise) {
    refreshPromise = axios
      .post<{ access_token: string }>(
        `${API_URL}/api/v1/auth/refresh`,
        {},
        { withCredentials: true },
      )
      .then((res) => {
        setAccessToken(res.data.access_token);
        return res.data.access_token;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
};

// Response interceptor to handle 401s and refresh the token
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // Never try to refresh in response to the refresh call itself.
    const isRefreshCall = originalRequest?.url?.includes('/auth/refresh');

    if (error.response?.status === 401 && !originalRequest?._retry && !isRefreshCall) {
      originalRequest._retry = true;

      try {
        const token = await refreshAccessToken();
        originalRequest.headers.Authorization = `Bearer ${token}`;
        return api(originalRequest);
      } catch (refreshError) {
        // If refresh fails (e.g., refresh token expired/invalid, or the account
        // was deactivated), the user must log in again.
        setAccessToken(null);
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('auth-expired'));
        }
        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  },
);
