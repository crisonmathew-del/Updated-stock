import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export const buttonStyles = cva(
  "inline-flex items-center justify-center gap-1.5 rounded-md text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        primary: "bg-tide text-background hover:opacity-90",
        secondary: "border border-border bg-surface-2 text-foreground hover:border-muted",
        ghost: "text-muted hover:bg-surface-2 hover:text-foreground",
      },
      size: { sm: "h-7 px-2.5", md: "h-9 px-3.5" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export function Button({
  className,
  variant,
  size,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonStyles>) {
  return (
    <button type={type} className={cn(buttonStyles({ variant, size }), className)} {...props} />
  );
}
