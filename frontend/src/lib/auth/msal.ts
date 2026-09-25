import { PublicClientApplication, type Configuration } from "@azure/msal-browser";

export const AZURE_CLIENT_ID = process.env.NEXT_PUBLIC_AZURE_CLIENT_ID ?? "";
/** Sign-in is switched off when no client ID is configured (e.g. a fresh local checkout). */
export const AUTH_ENABLED = AZURE_CLIENT_ID.length > 0;
export const API_SCOPE = `api://${AZURE_CLIENT_ID}/access_as_user`;
/** MSAL v5 returns every sign-in and sign-out through this "redirect bridge" page. */
export const REDIRECT_PATH = "/auth/redirect";
export const loginRequest = { scopes: [API_SCOPE] };

let instance: PublicClientApplication | null = null;

/** One shared instance. Safe during server rendering: MSAL detects there's no browser and stays inert. */
export function getMsalInstance(): PublicClientApplication {
    if (!instance) {
        const config: Configuration = {
            auth: {
                clientId: AZURE_CLIENT_ID,
                authority: "https://login.microsoftonline.com/common",
                redirectUri: REDIRECT_PATH, // resolved against the current origin
                postLogoutRedirectUri: REDIRECT_PATH,
            },
            cache: { cacheLocation: "sessionStorage" },
        };
        instance = new PublicClientApplication(config);
    }
    return instance;
}

let resolveReady: () => void = () => {};
/** Resolves once MsalProvider has initialised MSAL and processed any sign-in redirect. */
export const msalReady = new Promise<void>((resolve) => {
    resolveReady = resolve;
});

export function markMsalReady(): void {
    resolveReady();
}
