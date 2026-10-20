"use client";

import * as Popover from "@radix-ui/react-popover";
import { GLOSSARY, type GlossaryKey } from "@/lib/glossary";
import { cn } from "@/lib/utils";

/**
 * A small "?" that explains a term in plain English (Phase 8). Click or tap opens it (hover
 * alone doesn't work on phones); Esc or a click outside closes it.
 */
export function InfoTip({ term, className }: { term: GlossaryKey; className?: string }) {
  const entry = GLOSSARY[term];
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label={`What is ${entry.term}?`}
          className={cn(
            "inline-flex size-4 shrink-0 items-center justify-center rounded-full border border-border align-middle text-[10px] leading-none font-normal text-muted normal-case hover:border-muted hover:text-foreground",
            className,
          )}
        >
          ?
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          side="top"
          align="start"
          sideOffset={6}
          collisionPadding={12}
          className="z-40 w-72 rounded-lg border border-border bg-surface p-3 text-sm shadow-lg"
        >
          <p className="font-semibold">{entry.term}</p>
          <p className="mt-1 text-muted">{entry.plain}</p>
          <Popover.Arrow className="fill-border" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
