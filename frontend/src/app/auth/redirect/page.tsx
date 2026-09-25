"use client";

import { useEffect } from "react";

/** MSAL v5 redirect bridge: hands the sign-in response back to the page that started it. */
export default function AuthRedirectPage() {
    useEffect(() => {
        import("@azure/msal-browser/redirect-bridge")
            .then(({ broadcastResponseToMainFrame }) => broadcastResponseToMainFrame())
            .catch(() => window.location.replace("/"));
    }, []);

    return <p className="container mx-auto px-4 py-16 text-sm text-muted-foreground">Signing you in…</p>;
}
