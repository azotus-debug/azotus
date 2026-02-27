"use client";

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { RealtimeProvider } from "@/contexts/RealtimeContext";
import ToastContainer from "@/components/common/Toast";
import { useState } from 'react';

export default function ClientProviders({ children }: { children: React.ReactNode }) {
  // We initialize the QueryClient inside the component state to ensure
  // data is not shared between different users/requests in SSR
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 60 * 1000, // 1 minute stale time by default
        refetchOnWindowFocus: true,
      },
    },
  }));

  return (
    <QueryClientProvider client={queryClient}>
      <RealtimeProvider>
        {children}
        <ToastContainer />
      </RealtimeProvider>
      <ReactQueryDevtools initialIsOpen={false} />
    </QueryClientProvider>
  );
}
