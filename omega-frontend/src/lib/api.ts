/**
 * Authenticated fetch wrapper for Omega API.
 *
 * All API calls should use `apiFetch` instead of bare `fetch`.
 * It injects the JWT Authorization header and handles 401 redirects.
 */

const TOKEN_KEY = "omega_token";

// ---- Token storage ----

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setStoredToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearStoredToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

// ---- Authenticated fetch ----

export async function apiFetch(
  path: string,
  init?: RequestInit
): Promise<Response> {
  const token = getStoredToken();
  const headers = new Headers(init?.headers);

  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const res = await fetch(path, { ...init, headers });

  // On 401, redirect to login (unless we're already on /login)
  if (res.status === 401 && typeof window !== "undefined") {
    const isLoginPage = window.location.pathname === "/login";
    if (!isLoginPage) {
      clearStoredToken();
      window.location.href = "/login";
    }
  }

  return res;
}

// ---- API base URL ----

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

/**
 * Direct backend URL for large uploads that must bypass the Next.js rewrite proxy.
 * Uses the same hostname the browser is on (so omegapro.local stays omegapro.local)
 * but routes to the FastAPI port (8001) directly.
 */
export function getBackendDirectUrl(): string {
  if (typeof window === "undefined") return "http://127.0.0.1:8001";
  const override = process.env.NEXT_PUBLIC_BACKEND_URL;
  if (override) return override;
  return `${window.location.protocol}//${window.location.hostname}:8001`;
}
