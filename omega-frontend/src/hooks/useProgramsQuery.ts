import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useProgramsStore, Program } from '@/store/programs';
import { useRealtime } from '@/contexts/RealtimeContext';
import { useEffect } from 'react';
import { apiFetch, API_BASE } from '@/lib/api';

export function useProgramsQuery() {
    const queryClient = useQueryClient();
    const { socket, isConnected } = useRealtime();

    const query = useQuery({
        queryKey: ['programs'],
        queryFn: async (): Promise<Program[]> => {
            const res = await apiFetch(`${API_BASE}/api/v2/programs`);
            if (!res.ok) throw new Error(`Failed to fetch programs: ${res.status}`);
            return await res.json();
        },
        // The data is basically fresh until a Socket tells us otherwise
        staleTime: Infinity,
    });

    useEffect(() => {
        if (query.data) {
            // Keep Zustand mirrored for components still using the store directly.
            useProgramsStore.setState({ programs: query.data, loading: false, error: null });
        }
    }, [query.data]);

    useEffect(() => {
        useProgramsStore.setState({ loading: query.isPending });
    }, [query.isPending]);

    useEffect(() => {
        if (query.isError) {
            const message = query.error instanceof Error ? query.error.message : 'Failed to fetch programs';
            useProgramsStore.setState({ error: message, loading: false });
        }
    }, [query.isError, query.error]);

    // Listen to the Real-Time bridge
    useEffect(() => {
        if (!socket || !isConnected) return;

        const handleModelUpdate = (data: unknown) => {
            console.log('⚡ Background Sync Triggered by Realtime Socket:', data);

            // In a fully optimized system, we would inject the diff directly into the cache
            // queryClient.setQueryData(['programs'], (old: Program[]) => { ... })

            // For Phase 1, we aggressively invalidate the query. React Query will immediately
            // fetch the latest /programs and seamlessly update the UI without loading spinners.
            queryClient.invalidateQueries({ queryKey: ['programs'] });
        };

        socket.on('track_updated', handleModelUpdate);
        socket.on('programs_updated', handleModelUpdate); // Legacy support mapping

        return () => {
            socket.off('track_updated', handleModelUpdate);
            socket.off('programs_updated', handleModelUpdate);
        };
    }, [socket, isConnected, queryClient]);

    return query;
}
