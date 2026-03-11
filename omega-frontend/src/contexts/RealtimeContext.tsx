'use client';

import React, { createContext, useContext, useEffect, useState } from 'react';
import { io, Socket } from 'socket.io-client';
import { getStoredToken } from '@/lib/api';

interface RealtimeContextType {
    socket: Socket | null;
    isConnected: boolean;
    subscribeToJob: (jobId: string) => void;
    unsubscribeFromJob: (jobId: string) => void;
}

const RealtimeContext = createContext<RealtimeContextType>({
    socket: null,
    isConnected: false,
    subscribeToJob: () => { },
    unsubscribeFromJob: () => { },
});

const SOCKET_URL =
    process.env.NEXT_PUBLIC_SOCKET_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    'http://127.0.0.1:8001';

export const RealtimeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [socket, setSocket] = useState<Socket | null>(null);
    const [isConnected, setIsConnected] = useState(false);

    useEffect(() => {
        const token = getStoredToken();

        // Initialize standard polling transport that eagerly upgrades to websocket
        const socketInstance = io(SOCKET_URL, {
            reconnectionAttempts: 5,
            reconnectionDelay: 1000,
            auth: token ? { token } : undefined,
        });

        socketInstance.on('connect', () => {
            console.log('Connected to Omega Realtime Core');
            setIsConnected(true);
        });

        socketInstance.on('disconnect', () => {
            console.warn('Disconnected from Omega Realtime Core');
            setIsConnected(false);
        });

        socketInstance.on('system_status', (data) => {
            console.log('System Status:', data);
        });

        setSocket(socketInstance);

        return () => {
            socketInstance.disconnect();
        };
    }, []);

    const subscribeToJob = (jobId: string) => {
        if (socket && isConnected) {
            socket.emit('subscribe_to_job', { job_id: jobId });
        }
    };

    const unsubscribeFromJob = (jobId: string) => {
        void jobId;
        // We could add logic for leaving rooms, but navigating away automatically abandons the component mount
    };

    return (
        <RealtimeContext.Provider value={{ socket, isConnected, subscribeToJob, unsubscribeFromJob }}>
            {children}
        </RealtimeContext.Provider>
    );
};

export const useRealtime = () => useContext(RealtimeContext);
