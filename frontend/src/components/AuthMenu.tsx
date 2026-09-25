"use client";

import { LogIn, LogOut } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";

const ROLE_LABEL = { student: "Student", faculty: "Faculty", admin: "Admin" } as const;

export function AuthMenu() {
    const { enabled, ready, signedIn, me, profileError, signIn, signOut, refresh } = useAuth();
    if (!enabled) return null;
    if (!ready) return <span className="h-8 w-24 animate-pulse rounded-md bg-muted" aria-hidden />;

    if (!me && signedIn) {
        return (
            <div className="flex items-center gap-2 text-sm">
                <span className="text-foreground/60" title={profileError ?? undefined}>
                    Couldn&apos;t load your profile
                </span>
                <button onClick={() => void refresh()} className="text-foreground/60 hover:text-foreground underline-offset-2 hover:underline">
                    Retry
                </button>
                <button onClick={signOut} className="text-foreground/60 hover:text-foreground" aria-label="Sign out">
                    <LogOut className="h-4 w-4" />
                </button>
            </div>
        );
    }

    if (!me) {
        return (
            <button
                onClick={signIn}
                className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition-colors"
            >
                <LogIn className="h-4 w-4" /> Sign in with Microsoft
            </button>
        );
    }

    const role = me.role ? ROLE_LABEL[me.role] : "New user";
    const badge = me.role === "faculty" && !me.verified ? `${role} (trial)` : role;
    return (
        <div className="flex items-center gap-3 text-sm">
            <span className="hidden md:inline text-foreground/80">{me.name ?? me.email}</span>
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">{badge}</span>
            <button onClick={signOut} className="text-foreground/60 hover:text-foreground" aria-label="Sign out">
                <LogOut className="h-4 w-4" />
            </button>
        </div>
    );
}
