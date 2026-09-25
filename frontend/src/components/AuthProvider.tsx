"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { MsalProvider, useMsal } from "@azure/msal-react";
import { EventType, InteractionStatus, type AccountInfo, type EventMessage } from "@azure/msal-browser";
import { AUTH_ENABLED, REDIRECT_PATH, getMsalInstance, loginRequest, markMsalReady, msalReady } from "@/lib/auth/msal";
import { apiFetch, describeApiError } from "@/lib/api";

export type Role = "student" | "faculty" | "admin";
export type Limits = { generate: number | null; upload: number | null; syllabus: number | null };
export type Me = {
    id: number;
    name: string | null;
    email: string | null;
    role: Role | null;
    verified: boolean;
    limits: Limits; // null = unlimited
    usedToday: { generate: number; upload: number; syllabus: number };
};

type AuthState = {
    enabled: boolean; // sign-in is configured
    ready: boolean; // MSAL has started and /me has loaded (or there is no signed-in account)
    signedIn: boolean; // MSAL has an active account (independent of whether /me loaded)
    me: Me | null; // null when signed out
    profileError: string | null; // set when signed in but /me failed to load
    signIn: () => void;
    signOut: () => void;
    refresh: () => Promise<void>;
};

const signedOut: AuthState = {
    enabled: false,
    ready: true,
    signedIn: false,
    me: null,
    profileError: null,
    signIn: () => {},
    signOut: () => {},
    refresh: async () => {},
};

const AuthContext = createContext<AuthState>(signedOut);

export function useAuth(): AuthState {
    return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
    const pathname = usePathname();
    // The redirect bridge must not run MSAL itself: it hands the response back to the main window.
    if (!AUTH_ENABLED || pathname === REDIRECT_PATH) {
        return <SignedOutProvider>{children}</SignedOutProvider>;
    }
    return (
        <MsalProvider instance={getMsalInstance()}>
            <AuthStateProvider>{children}</AuthStateProvider>
        </MsalProvider>
    );
}

function SignedOutProvider({ children }: { children: ReactNode }) {
    useEffect(() => {
        markMsalReady();
    }, []);
    return <AuthContext.Provider value={signedOut}>{children}</AuthContext.Provider>;
}

function AuthStateProvider({ children }: { children: ReactNode }) {
    const { instance, accounts, inProgress } = useMsal();
    const accountId = accounts[0]?.homeAccountId ?? null;
    const [me, setMe] = useState<Me | null>(null);
    const [loaded, setLoaded] = useState(false);
    const [profileError, setProfileError] = useState<string | null>(null);

    // Remember who signed in so token requests know which account to use.
    useEffect(() => {
        const callbackId = instance.addEventCallback((event: EventMessage) => {
            if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
                instance.setActiveAccount(event.payload as AccountInfo);
            }
        });
        return () => {
            if (callbackId) instance.removeEventCallback(callbackId);
        };
    }, [instance]);

    useEffect(() => {
        if (inProgress === InteractionStatus.None) markMsalReady();
    }, [inProgress]);

    const refresh = useCallback(async () => {
        await msalReady;
        if (!accountId) {
            setMe(null);
            setProfileError(null);
            setLoaded(true);
            return;
        }
        try {
            const response = await apiFetch("/api/v1/me");
            setMe((await response.json()) as Me);
            setProfileError(null);
        } catch (error) {
            setMe(null);
            setProfileError(describeApiError(error));
        } finally {
            setLoaded(true);
        }
    }, [accountId]);

    useEffect(() => {
        if (inProgress === InteractionStatus.None) void refresh();
    }, [inProgress, refresh]);

    const value = useMemo<AuthState>(
        () => ({
            enabled: true,
            ready: loaded,
            signedIn: accountId !== null,
            me,
            profileError,
            signIn: () => void instance.loginRedirect(loginRequest),
            signOut: () => void instance.logoutRedirect(),
            refresh,
        }),
        [instance, loaded, accountId, me, profileError, refresh],
    );

    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
