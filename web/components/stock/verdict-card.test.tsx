import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DETAIL_WITH_VERDICT, WATCH } from "@/components/buy-list/fixtures";
import { renderWithClient } from "@/test-utils";
import { ActionLabel, VerdictCard, WatchCard } from "./verdict-card";

describe("VerdictCard", () => {
  it("says what to do, the order, and every rule with its numbers", () => {
    renderWithClient(<VerdictCard setup={DETAIL_WITH_VERDICT} />);
    const card = screen.getByRole("region", { name: "What to do" });
    expect(card).toHaveTextContent("3 of 3 rules pass");
    expect(card).toHaveTextContent("Get ready");
    expect(card).toHaveTextContent("place the order tonight");
    expect(card).toHaveTextContent("Place a buy-stop order for 259 shares at 92.56");

    const rules = within(card).getByRole("list", { name: "The app's buy rules" });
    const items = within(rules).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[1]).toHaveTextContent("FundamentalsFundamentals Grade B (needs C or better).");
    expect(within(items[1]).getByLabelText("passes")).toBeInTheDocument();
    expect(card).toHaveTextContent("In plain English");
    expect(card).toHaveTextContent("The app never places trades.");
  });

  it("explains a term with the ? button", async () => {
    renderWithClient(<VerdictCard setup={DETAIL_WITH_VERDICT} />);
    fireEvent.click(screen.getByRole("button", { name: "What is Buy-stop order?" }));
    expect(await screen.findByText(/An order you give your broker in advance/)).toBeInTheDocument();
  });

  it("shows nothing without a verdict", () => {
    const { container } = renderWithClient(
      <VerdictCard setup={{ ...DETAIL_WITH_VERDICT, verdict: null }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("WatchCard", () => {
  it("shows the readiness parts and the early entry", () => {
    renderWithClient(<WatchCard watch={WATCH} />);
    const card = screen.getByRole("region", { name: "Breakout watch" });
    expect(card).toHaveTextContent("78/100 readiness");
    expect(card).toHaveTextContent("On the watch list");
    const parts = within(card).getAllByRole("listitem");
    expect(parts[0]).toHaveTextContent("Close to the pivot23 / 25");
    expect(parts[1]).toHaveTextContent("no data");
    expect(card).toHaveTextContent("Early entry (riskier)");
    expect(card).toHaveTextContent("a buy-stop at 91.90");
  });
});

describe("ActionLabel", () => {
  it("pairs the mark with the words", () => {
    renderWithClient(<ActionLabel action="avoid" label="Don't buy" />);
    expect(screen.getByText("Don't buy")).toHaveTextContent("✕ Don't buy");
  });

  it("shows a dash without a verdict", () => {
    renderWithClient(<ActionLabel action={null} label={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
