"use client";

import { Lock } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";

export function AccessNotice({ title, message, showSignIn = false }: { title: string; message: string; showSignIn?: boolean }) {
    const { enabled, signedIn, signIn, refresh } = useAuth();
    return (
        <div className="container mx-auto max-w-xl px-4 py-24 text-center">
            <Lock className="mx-auto mb-4 h-10 w-10 text-primary" />
            <h1 className="mb-3 text-2xl font-bold">{title}</h1>
            <p className="mb-8 text-muted-foreground">{message}</p>
            {showSignIn && enabled && !signedIn && (
                <button onClick={signIn} className="rounded-lg bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground hover:bg-primary/90">
                    Sign in with Microsoft
                </button>
            )}
            {showSignIn && signedIn && (
                <button onClick={() => void refresh()} className="rounded-lg bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground hover:bg-primary/90">
                    Retry
                </button>
            )}
        </div>
    );
}
