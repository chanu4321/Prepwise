import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { API_BASE_URL } from "@/lib/utils";
import { AUTH_ENABLED, getMsalInstance, loginRequest, msalReady } from "@/lib/auth/msal";

export class ApiRequestError extends Error {
    constructor(
        public status: number,
        public code: string,
        message: string,
        public retryAfterSeconds: number | null = null,
    ) {
        super(message);
    }
}

/** The signed-in user's access token, or null when signed out (requests then go out anonymously). */
async function getAccessToken(): Promise<string | null> {
    if (!AUTH_ENABLED) return null;
    await msalReady;
    const msal = getMsalInstance();
    const account = msal.getActiveAccount() ?? msal.getAllAccounts()[0];
    if (!account) return null;
    try {
        const result = await msal.acquireTokenSilent({ ...loginRequest, account });
        return result.accessToken;
    } catch (error) {
        if (error instanceof InteractionRequiredAuthError) {
            await msal.acquireTokenRedirect({ ...loginRequest, account });
            return null; // the page is navigating away
        }
        throw error;
    }
}

/** fetch() against the API with the user's token attached. Throws ApiRequestError on non-2xx responses. */
export async function apiFetch(path: string, init: RequestInit = {}, baseUrl: string = API_BASE_URL): Promise<Response> {
    const token = await getAccessToken();
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${baseUrl}${path}`, { ...init, headers });
    if (!response.ok) {
        let body: { detail?: unknown; code?: unknown } = {};
        try {
            body = await response.json();
        } catch {
            // non-JSON error body
        }
        const retryAfter = Number(response.headers.get("Retry-After"));
        throw new ApiRequestError(
            response.status,
            typeof body.code === "string" ? body.code : "error",
            typeof body.detail === "string" ? body.detail : "Something went wrong. Please try again.",
            Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
        );
    }
    return response;
}

export function describeApiError(error: unknown): string {
    if (error instanceof ApiRequestError) {
        if (error.status === 429 && error.retryAfterSeconds) {
            const hours = Math.max(1, Math.round(error.retryAfterSeconds / 3600));
            return `${error.message} (about ${hours} hour${hours === 1 ? "" : "s"} from now)`;
        }
        return error.message;
    }
    return "Couldn't reach the server. Check your connection and try again.";
}
