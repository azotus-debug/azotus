"use client";

import SSEProvider from "@/components/SSEProvider";
import ToastContainer from "@/components/common/Toast";

export default function ClientProviders({ children }: { children: React.ReactNode }) {
  return (
    <SSEProvider>
      {children}
      <ToastContainer />
    </SSEProvider>
  );
}
