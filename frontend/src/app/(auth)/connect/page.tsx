import { ConnectPageClient } from "./page-client";

type ConnectPageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

function resolveOauthErrorMessage(error: string | null): string | null {
  switch (error) {
    case "oauth_expired":
      return "Connection timed out before Google redirected back. Please try again.";
    case "oauth_invalid_state":
      return "This Google connect attempt is no longer valid. Please try again.";
    case "google_denied":
      return "Google access was not granted. Please try again if you still want to connect.";
    case "missing_code":
      return "Google did not return an authorization code. Please try again.";
    case "missing_refresh_token":
      return "Google did not return a refresh token. Disconnect any existing consent and try again.";
    case "token_exchange_failed":
      return "Google token exchange failed. Please try again.";
    case "userinfo_failed":
      return "Felix could not verify your Google account details. Please try again.";
    case "unknown_error":
      return "Google connection failed. Please try again.";
    default:
      return null;
  }
}

export default async function ConnectPage({ searchParams }: ConnectPageProps) {
  const resolvedParams = (await searchParams) ?? {};
  const errorParam = resolvedParams.error;
  const errorCode = Array.isArray(errorParam) ? errorParam[0] : errorParam;

  return <ConnectPageClient initialError={resolveOauthErrorMessage(errorCode ?? null)} />;
}
