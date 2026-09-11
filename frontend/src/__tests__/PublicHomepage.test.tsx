import "@testing-library/jest-dom";
import { render, screen, within } from "@testing-library/react";

const getUser = jest.fn();
jest.mock("@supabase/ssr", () => ({
  createServerClient: () => ({ auth: { getUser } }),
}));
jest.mock("next/headers", () => ({
  cookies: () => ({ getAll: () => [], set: jest.fn() }),
}));
jest.mock("next/navigation", () => ({
  redirect: (to: string) => { throw new Error(`NEXT_REDIRECT:${to}`); },
}));

import RootPage from "@/app/page";

const originalRequestAccessUrl = process.env.NEXT_PUBLIC_REQUEST_ACCESS_URL;

beforeEach(() => {
  getUser.mockReset();
  getUser.mockResolvedValue({ data: { user: null } });
  delete process.env.NEXT_PUBLIC_REQUEST_ACCESS_URL;
});

afterEach(() => {
  if (originalRequestAccessUrl === undefined) {
    delete process.env.NEXT_PUBLIC_REQUEST_ACCESS_URL;
  } else {
    process.env.NEXT_PUBLIC_REQUEST_ACCESS_URL = originalRequestAccessUrl;
  }
});

it("serves the public dossier at / and keeps login links on the existing route", async () => {
  render(await RootPage());

  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
    "I read the four hundred. You read the nine.",
  );
  expect(screen.getAllByRole("link", { name: /log in/i })).toHaveLength(2);
  screen.getAllByRole("link", { name: /log in/i }).forEach((link) => {
    expect(link).toHaveAttribute("href", "/login");
  });
  expect(getUser).toHaveBeenCalledTimes(1);
});

it("keeps authenticated visitors going to /home", async () => {
  getUser.mockResolvedValue({ data: { user: { id: "user-1" } } });

  await expect(RootPage()).rejects.toThrow("NEXT_REDIRECT:/home");
});

it("uses the existing configured access form in the header and both invitations", async () => {
  process.env.NEXT_PUBLIC_REQUEST_ACCESS_URL = "https://example.com/existing-access-form";
  render(await RootPage());

  const links = screen.getAllByRole("link", { name: /request access/i });
  expect(links).toHaveLength(3);
  links.forEach((link) => {
    expect(link).toHaveAttribute("href", "https://example.com/existing-access-form");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});

it("preserves the existing absence of a request link when no form is configured", async () => {
  render(await RootPage());

  expect(screen.queryByRole("link", { name: /request access/i })).not.toBeInTheDocument();
});

it("lets visitors trace specimen evidence to the corresponding earlier scene", async () => {
  render(await RootPage());

  const source = screen.getByRole("link", { name: /source: monday’s email/i });
  expect(source).toHaveAttribute("href", "#launch-approval");
  expect(document.getElementById("launch-approval")).toHaveTextContent("Email from Maya");
  expect(screen.getByText("Illustrative examples throughout", { exact: false })).toBeVisible();
});

it("provides a focusable skip target and meaningful briefing and list semantics", async () => {
  render(await RootPage());

  expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  const main = screen.getByRole("main");
  expect(main).toHaveAttribute("tabindex", "-1");
  main.focus();
  expect(main).toHaveFocus();
  expect(screen.getByRole("region", { name: /the morning\s*page/i })).toBeVisible();
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  expect(within(screen.getByRole("list", { name: "Context Felix works with" })).getAllByRole("listitem")).toHaveLength(4);
  const timeline = screen.getByRole("list", { name: "Website Launch timeline" });
  expect(timeline).toHaveAttribute("role", "list");
  expect(within(timeline).getAllByRole("listitem")).toHaveLength(4);
});

it("styles only the initial letter of I’m as a drop cap", async () => {
  const { container } = render(await RootPage());
  const initial = container.querySelector(".dropCap");
  expect(initial).toHaveTextContent(/^I$/);
  expect(initial?.parentElement?.textContent).toMatch(/^I’m Felix\./);
});

it("reserves external arrows and new-tab announcements for request-access links", async () => {
  process.env.NEXT_PUBLIC_REQUEST_ACCESS_URL = "https://example.com/existing-access-form";
  render(await RootPage());

  screen.getAllByRole("link").forEach((link) => {
    if (link.getAttribute("target") === "_blank") {
      expect(link).toHaveTextContent("↗");
      expect(link).toHaveAccessibleName(/opens in a new tab/i);
    } else {
      expect(link).not.toHaveTextContent("↗");
    }
  });
});

describe("public homepage metadata", () => {
  const originalSiteUrl = process.env.NEXT_PUBLIC_SITE_URL;
  const originalProductionUrl = process.env.VERCEL_PROJECT_PRODUCTION_URL;

  afterEach(() => {
    for (const [key, value] of [
      ["NEXT_PUBLIC_SITE_URL", originalSiteUrl],
      ["VERCEL_PROJECT_PRODUCTION_URL", originalProductionUrl],
    ]) {
      if (value === undefined) delete process.env[key!];
      else process.env[key!] = value;
    }
  });

  it("uses the company origin for canonical and social URLs, with a cream light viewport", () => {
    process.env.NEXT_PUBLIC_SITE_URL = "https://company.example";
    process.env.VERCEL_PROJECT_PRODUCTION_URL = "production.vercel.app";
    jest.isolateModules(() => {
      const { metadata, viewport } = require("@/app/page");
      expect(metadata.metadataBase.href).toBe("https://company.example/");
      expect(metadata.alternates.canonical).toBe("/");
      expect(metadata.openGraph).toMatchObject({
        type: "website",
        siteName: "Felix",
        title: metadata.title,
        description: metadata.description,
        images: [{ url: "https://company.example/icon-512.png", width: 512, height: 512, alt: "Felix" }],
      });
      expect(metadata.twitter).toMatchObject({ card: "summary", title: metadata.title, description: metadata.description });
      expect(viewport).toEqual({ themeColor: "#f4f1ea", colorScheme: "light" });
    });
  });

  it("falls back to Vercel’s production domain", () => {
    delete process.env.NEXT_PUBLIC_SITE_URL;
    process.env.VERCEL_PROJECT_PRODUCTION_URL = "production.vercel.app";
    jest.isolateModules(() => {
      const { metadata } = require("@/app/page");
      expect(metadata.metadataBase.href).toBe("https://production.vercel.app/");
    });
  });

  it("omits absolute URLs when no public origin is known", () => {
    delete process.env.NEXT_PUBLIC_SITE_URL;
    delete process.env.VERCEL_PROJECT_PRODUCTION_URL;
    jest.isolateModules(() => {
      const { metadata } = require("@/app/page");
      expect(metadata.metadataBase).toBeUndefined();
      expect(metadata.alternates).toBeUndefined();
      expect(metadata.openGraph.url).toBeUndefined();
      expect(metadata.openGraph.images).toBeUndefined();
      expect(metadata.twitter.images).toBeUndefined();
    });
  });
});
