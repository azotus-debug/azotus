"use client";

import { createContext, useContext, type ReactNode } from "react";
import { useSSE, type SSEConnection } from "@/hooks/useSSE";

const SSEContext = createContext<SSEConnection | null>(null);

export function useSSEContext(): SSEConnection {
  const ctx = useContext(SSEContext);
  if (!ctx) {
    throw new Error("useSSEContext must be used within an SSEProvider");
  }
  return ctx;
}

interface SSEProviderProps {
  children: ReactNode;
}

function SSEProvider({ children }: SSEProviderProps) {
  const sse = useSSE();

  return <SSEContext.Provider value={sse}>{children}</SSEContext.Provider>;
}

export default SSEProvider;
