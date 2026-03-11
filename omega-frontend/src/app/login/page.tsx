"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const router = useRouter();

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      await login(username, password);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center"
         style={{ background: "rgb(var(--omega-bg))" }}>
      <div className="w-full max-w-sm p-8 rounded-xl"
           style={{ background: "rgb(var(--omega-surface-1))", border: "1px solid rgba(var(--omega-border), 0.06)" }}>
        <div className="text-center mb-8">
          <h1 className="text-2xl font-semibold" style={{ color: "rgb(var(--omega-text-1))" }}>
            Omega Editor
          </h1>
          <p className="mt-2 text-sm" style={{ color: "rgb(var(--omega-text-3))" }}>
            Sign in to continue
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="username" className="block text-xs font-medium mb-1.5"
                   style={{ color: "rgb(var(--omega-text-2))" }}>
              Username
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              required
              className="w-full px-3 py-2 rounded-lg text-sm outline-none transition-colors"
              style={{
                background: "rgb(var(--omega-surface-2))",
                border: "1px solid rgba(var(--omega-border), 0.08)",
                color: "rgb(var(--omega-text-1))",
              }}
              placeholder="Enter username"
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-xs font-medium mb-1.5"
                   style={{ color: "rgb(var(--omega-text-2))" }}>
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full px-3 py-2 rounded-lg text-sm outline-none transition-colors"
              style={{
                background: "rgb(var(--omega-surface-2))",
                border: "1px solid rgba(var(--omega-border), 0.08)",
                color: "rgb(var(--omega-text-1))",
              }}
              placeholder="Enter password"
            />
          </div>

          {error && (
            <p className="text-xs px-1" style={{ color: "rgb(var(--omega-red))" }}>
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading || !username || !password}
            className="w-full py-2.5 rounded-lg text-sm font-medium transition-opacity disabled:opacity-50"
            style={{
              background: "rgb(var(--omega-blue))",
              color: "#fff",
            }}
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
