import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const api = axios.create({
  baseURL: API_URL,
  withCredentials: true, // Crucial for sending/receiving the HttpOnly refresh token
});

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

// Response interceptor to handle 401s and refresh the token
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    
    // If the error is 401 and we haven't retried this specific request yet
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;
      
      try {
        // Call the refresh endpoint. Since withCredentials is true, the HttpOnly cookie is sent automatically.
        const res = await axios.post(`${API_URL}/api/v1/auth/refresh`, {}, { withCredentials: true });
        
        // Save the new access token in memory
        setAccessToken(res.data.access_token);
        
        // Update the original request's auth header
        originalRequest.headers.Authorization = `Bearer ${res.data.access_token}`;
        
        // Retry the original request
        return api(originalRequest);
      } catch (refreshError) {
        // If refresh fails (e.g., refresh token expired/invalid), user must log in again
        setAccessToken(null);
        // Optionally redirect to login, or emit an event
        if (typeof window !== 'undefined') {
            window.dispatchEvent(new CustomEvent('auth-expired'));
        }
        return Promise.reject(refreshError);
      }
    }
    
    return Promise.reject(error);
  }
);
