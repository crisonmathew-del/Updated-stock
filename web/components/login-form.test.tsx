import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "@/test-utils";
import { LoginForm } from "./login-form";

const replace = vi.fn();
let search = "next=/admin/data";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => new URLSearchParams(search),
}));

function fillAndSubmit(email: string, password: string) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

beforeEach(() => {
  replace.mockReset();
  search = "next=/admin/data";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LoginForm", () => {
  it("signs in and returns to the page that asked for it", async () => {
    const fetchMock = mockApi({ "POST /api/auth/login": { body: { email: "me@example.com" } } });
    render(<LoginForm />);

    fillAndSubmit("me@example.com", "correct horse battery staple");

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/admin/data"));
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      email: "me@example.com",
      password: "correct horse battery staple",
    });
  });

  it("shows the API's message when sign-in fails", async () => {
    mockApi({
      "POST /api/auth/login": { status: 401, body: { detail: "Email or password is incorrect." } },
    });
    render(<LoginForm />);

    fillAndSubmit("me@example.com", "wrong");

    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
    expect(replace).not.toHaveBeenCalled();
  });

  it("ignores off-site redirect targets", async () => {
    search = "next=//evil.example";
    mockApi({ "POST /api/auth/login": { body: { email: "me@example.com" } } });
    render(<LoginForm />);

    fillAndSubmit("me@example.com", "correct horse battery staple");

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });
});
